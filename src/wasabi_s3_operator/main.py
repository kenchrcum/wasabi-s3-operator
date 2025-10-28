"""Main entry point for the Wasabi S3 Operator Operator."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import kopf
from prometheus_client import start_http_server

from . import logging as structured_logging
from . import metrics
from .builders.bucket import create_bucket_config_from_spec
from .builders.provider import create_provider_from_spec
from .constants import (
    API_GROUP_VERSION,
    KIND_ACCESS_KEY,
    KIND_BUCKET,
    KIND_BUCKET_POLICY,
    KIND_IAM_POLICY,
    KIND_PROVIDER,
    KIND_USER,
)
from .utils.conditions import (
    set_apply_failed_condition,
    set_attach_failed_condition,
    set_bucket_not_ready_condition,
    set_creation_failed_condition,
    set_policy_invalid_condition,
    set_provider_not_ready_condition,
    set_rotation_failed_condition,
    set_auth_valid_condition,
    set_endpoint_reachable_condition,
    set_ready_condition,
)
from .utils.access_keys import create_access_key_secret
from .utils.events import (
    emit_access_key_created,
    emit_access_key_rotated,
    emit_bucket_created,
    emit_bucket_deleted,
    emit_bucket_updated,
    emit_policy_applied,
    emit_policy_failed,
    emit_reconcile_failed,
    emit_reconcile_started,
    emit_validate_failed,
    emit_validate_succeeded,
)


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_: Any) -> None:
    """Configure the operator."""
    # Set up structured JSON logging
    structured_logging.setup_structured_logging()

    # Configure persistence
    # Use AnnotationsProgressStorage to avoid conflicts with status updates
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage()
    settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage()

    settings.posting.level = 0
    settings.networking.request_timeout = 30.0
    settings.execution.max_workers = 4

    # Start metrics HTTP server on port 8080
    metrics_port = int(os.getenv("METRICS_PORT", "8080"))
    start_http_server(metrics_port)


@kopf.on.create(API_GROUP_VERSION, KIND_PROVIDER)
@kopf.on.update(API_GROUP_VERSION, KIND_PROVIDER)
@kopf.on.resume(API_GROUP_VERSION, KIND_PROVIDER)
def handle_provider(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle Provider resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")
    uid = meta.get("uid", "unknown")

    emit_reconcile_started(meta)

    # Track reconciliation
    metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        if not spec.get("endpoint") or not spec.get("region"):
            error_msg = "endpoint and region are required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Initialize conditions if needed
        conditions = status.get("conditions", [])

        # Try to create provider and test connectivity
        try:
            provider = create_provider_from_spec(spec, meta)
            auth_valid = True
            auth_message = "Authentication successful"
        except Exception as e:
            auth_valid = False
            auth_message = f"Authentication failed: {str(e)}"
            logger.error(f"Failed to create provider for {name}: {e}")

        conditions = set_auth_valid_condition(conditions, auth_valid, auth_message)

        # Test connectivity
        if auth_valid:
            try:
                connected = provider.test_connectivity()
                endpoint_message = "Endpoint is reachable" if connected else "Endpoint is unreachable"
                # Track connectivity status
                metrics.provider_connectivity_total.labels(provider=name, status="connected" if connected else "disconnected").inc()
            except Exception as e:
                connected = False
                endpoint_message = f"Connectivity test failed: {str(e)}"
                logger.error(f"Connectivity test failed for {name}: {e}")
                metrics.provider_connectivity_total.labels(provider=name, status="error").inc()
        else:
            connected = False
            endpoint_message = "Cannot test connectivity due to auth failure"

        conditions = set_endpoint_reachable_condition(conditions, connected, endpoint_message)

        # Set overall ready condition
        ready = auth_valid and connected
        ready_message = "Provider is ready" if ready else "Provider is not ready"
        conditions = set_ready_condition(conditions, ready, ready_message)

        # Update status
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "connected": connected,
            "lastConnectTime": datetime.now(timezone.utc).isoformat() if connected else None,
            "conditions": conditions,
        }

        if ready:
            metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="success").inc()
        else:
            metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="failed").inc()

        # Update status directly via patch to avoid conflicts
        patch.status.update(status_update)
    except Exception as e:
        logger.error(f"Provider reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_PROVIDER).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_PROVIDER)
def handle_provider_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle Provider resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")

    logger.info(f"Provider {name} is being deleted")
    # No cleanup needed for provider itself


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET)
def handle_bucket(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle Bucket resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_BUCKET, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        bucket_name = spec.get("name")
        provider_ref = spec.get("providerRef", {})

        if not bucket_name:
            error_msg = "bucket name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
            raise ValueError(error_msg)

        provider_name = provider_ref.get("name")
        if not provider_name:
            error_msg = "providerRef.name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Get provider
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        api = client.CustomObjectsApi()
        provider_ns = provider_ref.get("namespace", namespace)

        try:
            provider_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=provider_ns,
                plural="providers",
                name=provider_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                # Don't re-raise - return to stop retries until provider is created
                return
            raise

        # Check if provider is ready
        provider_status = provider_obj.get("status", {})
        provider_conditions = provider_status.get("conditions", [])
        provider_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in provider_conditions
        )

        if not provider_ready:
            error_msg = f"Provider {provider_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_provider_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            raise kopf.TemporaryError(error_msg)

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Create bucket configuration
        bucket_config = create_bucket_config_from_spec(spec, provider_spec.get("region", "us-east-1"))

        # Check if bucket exists
        bucket_exists = provider_client.bucket_exists(bucket_name)

        conditions = status.get("conditions", [])

        if not bucket_exists:
            # Create bucket
            try:
                provider_client.create_bucket(bucket_name, bucket_config)
                emit_bucket_created(meta, bucket_name)
                logger.info(f"Created bucket {bucket_name}")
                metrics.bucket_operations_total.labels(operation="create", result="success").inc()
            except Exception as e:
                error_msg = f"Failed to create bucket: {str(e)}"
                logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
                metrics.bucket_operations_total.labels(operation="create", result="failed").inc()
                patch.status.update({
                    "exists": False,
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
        else:
            emit_bucket_updated(meta, bucket_name)
            logger.info(f"Bucket {bucket_name} already exists")
            metrics.bucket_operations_total.labels(operation="exists", result="success").inc()

        # Handle auto-management if enabled
        auto_manage = spec.get("autoManage", {})
        auto_manage_enabled = auto_manage.get("enabled", True)
        
        # Initialize variables for status update
        accesskey_crd_name = None
        
        if auto_manage_enabled:
            try:
                # Determine user name
                user_name = auto_manage.get("userName", bucket_name)
                access_level = auto_manage.get("accessLevel", "readwrite")
                
                # Initialize accesskey_crd_name for status update
                accesskey_crd_name = f"{name}-accesskey"
                
                # Step 1: Create User with inline IAM policy
                user_crd_name = f"{name}-user"

                # Determine actions based on access level for inline policy
                actions = []
                if access_level == "readonly":
                    actions = ["s3:GetObject", "s3:ListBucket"]
                elif access_level == "readwrite":
                    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
                else:  # full
                    actions = ["s3:*"]

                # Create inline IAM policy for the user
                user_policy = {
                    "version": "2012-10-17",
                    "statement": [
                        {
                            "effect": "Allow",
                            "action": actions,
                            "resource": [
                                f"arn:aws:s3:::{bucket_name}",
                                f"arn:aws:s3:::{bucket_name}/*",
                            ],
                        }
                    ],
                }

                # Check if user already exists
                user_ready = False
                try:
                    existing_user = api.get_namespaced_custom_object(
                        group="s3.cloud37.dev",
                        version="v1alpha1",
                        namespace=namespace,
                        plural="users",
                        name=user_crd_name,
                    )
                    logger.info(f"User {user_crd_name} already exists")
                    # Check if user is ready
                    user_status = existing_user.get("status", {})
                    user_conditions = user_status.get("conditions", [])
                    user_ready = any(
                        cond.get("type") == "Ready" and cond.get("status") == "True" for cond in user_conditions
                    )
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        # Create user with inline policy
                        import json
                        user_body = {
                            "apiVersion": "s3.cloud37.dev/v1alpha1",
                            "kind": "User",
                            "metadata": {
                                "name": user_crd_name,
                                "namespace": namespace,
                                "ownerReferences": [
                                    {
                                        "apiVersion": "s3.cloud37.dev/v1alpha1",
                                        "kind": "Bucket",
                                        "name": name,
                                        "uid": meta.get("uid"),
                                        "controller": True,
                                    }
                                ],
                            },
                            "spec": {
                                "providerRef": {"name": provider_name, "namespace": provider_ns},
                                "name": user_name,
                                "policy": user_policy,
                                "tags": {"ManagedBy": "wasabi-s3-operator", "Bucket": bucket_name},
                            },
                        }
                        logger.info(f"Creating User CRD {user_crd_name} with policy: {user_policy}")
                        api.create_namespaced_custom_object(
                            group="s3.cloud37.dev",
                            version="v1alpha1",
                            namespace=namespace,
                            plural="users",
                            body=user_body,
                        )
                        logger.info(f"Created user {user_crd_name} with inline policy")
                        
                        # Wait for user to be ready before creating access key
                        max_wait_time = 60  # Maximum wait time in seconds
                        wait_interval = 1  # Check every second
                        elapsed_time = 0
                        
                        while not user_ready and elapsed_time < max_wait_time:
                            time.sleep(wait_interval)
                            elapsed_time += wait_interval
                            
                            try:
                                user_obj = api.get_namespaced_custom_object(
                                    group="s3.cloud37.dev",
                                    version="v1alpha1",
                                    namespace=namespace,
                                    plural="users",
                                    name=user_crd_name,
                                )
                                user_status = user_obj.get("status", {})
                                user_conditions = user_status.get("conditions", [])
                                user_ready = any(
                                    cond.get("type") == "Ready" and cond.get("status") == "True" for cond in user_conditions
                                )
                                
                                if user_ready:
                                    logger.info(f"User {user_crd_name} is now ready")
                                else:
                                    logger.debug(f"Waiting for user {user_crd_name} to be ready... ({elapsed_time}s)")
                            except client.exceptions.ApiException:
                                # User not found yet, continue waiting
                                pass
                        
                        if not user_ready:
                            logger.warning(f"User {user_crd_name} not ready after {max_wait_time}s, proceeding anyway")
                    else:
                        raise
                
                # Step 2: Create AccessKey (only if user is ready)
                if user_ready:
                    try:
                        existing_key = api.get_namespaced_custom_object(
                            group="s3.cloud37.dev",
                            version="v1alpha1",
                            namespace=namespace,
                            plural="accesskeys",
                            name=accesskey_crd_name,
                        )
                        logger.info(f"AccessKey {accesskey_crd_name} already exists")
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            rotation_config = auto_manage.get("rotation", {})
                            accesskey_body = {
                                "apiVersion": "s3.cloud37.dev/v1alpha1",
                                "kind": "AccessKey",
                                "metadata": {
                                    "name": accesskey_crd_name,
                                    "namespace": namespace,
                                    "ownerReferences": [
                                        {
                                            "apiVersion": "s3.cloud37.dev/v1alpha1",
                                            "kind": "Bucket",
                                            "name": name,
                                            "uid": meta.get("uid"),
                                            "controller": True,
                                        }
                                    ],
                                },
                                "spec": {
                                    "providerRef": {"name": provider_name, "namespace": provider_ns},
                                    "userRef": {"name": user_crd_name},
                                    "displayName": f"Access key for bucket {bucket_name}",
                                    "rotate": rotation_config,
                                },
                            }
                            api.create_namespaced_custom_object(
                                group="s3.cloud37.dev",
                                version="v1alpha1",
                                namespace=namespace,
                                plural="accesskeys",
                                body=accesskey_body,
                            )
                            logger.info(f"Created access key {accesskey_crd_name}")
                        else:
                            raise
                else:
                    logger.warning(f"Skipping AccessKey creation for {name} as user {user_crd_name} is not ready")
                
                # Step 3: Create BucketPolicy to grant user access to bucket
                bucketpolicy_crd_name = f"{name}-policy"
                try:
                    existing_policy = api.get_namespaced_custom_object(
                        group="s3.cloud37.dev",
                        version="v1alpha1",
                        namespace=namespace,
                        plural="bucketpolicies",
                        name=bucketpolicy_crd_name,
                    )
                    logger.info(f"BucketPolicy {bucketpolicy_crd_name} already exists")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        # Create bucket policy that allows the user to access the bucket
                        # Use the IAM user name (not CRD name) in the ARN
                        user_arn = f"arn:aws:iam::*:user/{user_name}"
                        
                        bucketpolicy_body = {
                            "apiVersion": "s3.cloud37.dev/v1alpha1",
                            "kind": "BucketPolicy",
                            "metadata": {
                                "name": bucketpolicy_crd_name,
                                "namespace": namespace,
                                "ownerReferences": [
                                    {
                                        "apiVersion": "s3.cloud37.dev/v1alpha1",
                                        "kind": "Bucket",
                                        "name": name,
                                        "uid": meta.get("uid"),
                                        "controller": True,
                                    }
                                ],
                            },
                            "spec": {
                                "bucketRef": {"name": name, "namespace": namespace},
                                "policy": {
                                    "version": "2012-10-17",
                                    "statement": [
                                        {
                                            "sid": f"Allow-{user_name}-Access",
                                            "effect": "Allow",
                                            "principal": user_arn,
                                            "action": actions,
                                            "resource": [
                                                f"arn:aws:s3:::{bucket_name}",
                                                f"arn:aws:s3:::{bucket_name}/*",
                                            ],
                                        }
                                    ],
                                },
                            },
                        }
                        logger.info(f"Creating BucketPolicy {bucketpolicy_crd_name} for user {user_name}")
                        api.create_namespaced_custom_object(
                            group="s3.cloud37.dev",
                            version="v1alpha1",
                            namespace=namespace,
                            plural="bucketpolicies",
                            body=bucketpolicy_body,
                        )
                        logger.info(f"Created bucket policy {bucketpolicy_crd_name}")
                    else:
                        raise
            
            except Exception as e:
                logger.error(f"Failed to auto-manage resources for bucket {bucket_name}: {e}")
                # Don't fail bucket creation if auto-management fails

        # Set ready condition
        conditions = set_ready_condition(conditions, True, f"Bucket {bucket_name} is ready")

        # Update status
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "bucketName": bucket_name,
            "exists": True,
            "lastSyncTime": datetime.now(timezone.utc).isoformat(),
            "conditions": conditions,
        }
        
        # Add credentials secret reference if auto-management is enabled
        if auto_manage_enabled and accesskey_crd_name:
            status_update["credentialsSecret"] = f"{accesskey_crd_name}-credentials"

        metrics.reconcile_total.labels(kind=KIND_BUCKET, result="success").inc()
        patch.status.update(status_update)
    except Exception as e:
        logger.error(f"Bucket reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_BUCKET, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_BUCKET).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_BUCKET)
def handle_bucket_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle Bucket resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")
    bucket_name = spec.get("name")

    logger.info(f"Bucket {name} is being deleted")

    if bucket_name:
        try:
            # Get provider
            provider_ref = spec.get("providerRef", {})
            provider_name = provider_ref.get("name")

            if provider_name:
                from kubernetes import client, config

                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()

                api = client.CustomObjectsApi()
                namespace = meta.get("namespace", "default")
                provider_ns = provider_ref.get("namespace", namespace)

                provider_obj = api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=provider_ns,
                    plural="providers",
                    name=provider_name,
                )

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Delete bucket
                if provider_client.bucket_exists(bucket_name):
                    provider_client.delete_bucket(bucket_name)
                    emit_bucket_deleted(meta, bucket_name)
                    logger.info(f"Deleted bucket {bucket_name}")
        except Exception as e:
            logger.error(f"Failed to delete bucket {bucket_name}: {e}")
            # Don't fail deletion if cleanup fails


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET_POLICY)
def handle_bucket_policy(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle BucketPolicy resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    logger.info(f"Starting bucket policy reconciliation for {name} in namespace {namespace}")
    logger.info(f"BucketPolicy spec: {spec}")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        bucket_ref = spec.get("bucketRef", {})
        policy = spec.get("policy", {})

        bucket_name = bucket_ref.get("name")
        if not bucket_name:
            error_msg = "bucketRef.name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        if not policy:
            error_msg = "policy is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        # Validate policy document structure
        if not isinstance(policy, dict) or "statement" not in policy:
            error_msg = "policy must contain 'statement' field"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Get bucket
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        api = client.CustomObjectsApi()
        bucket_ns = bucket_ref.get("namespace", namespace)

        try:
            bucket_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=bucket_ns,
                plural="buckets",
                name=bucket_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"Bucket {bucket_name} not found in namespace {bucket_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_bucket_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                # Don't re-raise - return to stop retries until bucket is created
                return
            raise

        # Check if bucket is ready
        bucket_status = bucket_obj.get("status", {})
        bucket_conditions = bucket_status.get("conditions", [])
        bucket_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in bucket_conditions
        )

        if not bucket_ready:
            error_msg = f"Bucket {bucket_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_bucket_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            # Raise exception to trigger retry
            raise kopf.TemporaryError(error_msg)

        # Get bucket spec to find provider
        bucket_spec = bucket_obj.get("spec", {})
        provider_ref = bucket_spec.get("providerRef", {})
        provider_name = provider_ref.get("name")

        if not provider_name:
            error_msg = "Bucket provider reference not found"
            logger.error(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_bucket_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })

        # Get provider
        provider_ns = provider_ref.get("namespace", bucket_ns)
        provider_obj = api.get_namespaced_custom_object(
            group="s3.cloud37.dev",
            version="v1alpha1",
            namespace=provider_ns,
            plural="providers",
            name=provider_name,
        )

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Apply policy
        conditions = status.get("conditions", [])

        try:
            # Check if bucket exists
            if not provider_client.bucket_exists(bucket_name):
                error_msg = f"Bucket {bucket_name} does not exist in provider"
                logger.error(error_msg)
                conditions = set_bucket_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })

            # Apply policy
            provider_client.set_bucket_policy(bucket_name, policy)
            emit_policy_applied(meta, bucket_name)
            logger.info(f"Applied policy to bucket {bucket_name}")

            # Set ready condition
            conditions = set_ready_condition(conditions, True, f"Policy applied to bucket {bucket_name}")

        except Exception as e:
            error_msg = f"Failed to apply policy: {str(e)}"
            logger.error(error_msg)
            conditions = set_apply_failed_condition(conditions, error_msg)
            emit_policy_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
            patch.status.update({
                "applied": False,
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })

        # Update status
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "applied": True,
            "lastSyncTime": datetime.now(timezone.utc).isoformat(),
            "conditions": conditions,
        }

        metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="success").inc()
        patch.status.update(status_update)
    except Exception as e:
        logger.error(f"BucketPolicy reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_BUCKET_POLICY).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_BUCKET_POLICY)
def handle_bucket_policy_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle BucketPolicy resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")
    bucket_ref = spec.get("bucketRef", {})
    bucket_name = bucket_ref.get("name")

    logger.info(f"BucketPolicy {name} is being deleted")

    if bucket_name:
        try:
            # Get bucket to find provider
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

            api = client.CustomObjectsApi()
            namespace = meta.get("namespace", "default")
            bucket_ns = bucket_ref.get("namespace", namespace)

            bucket_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=bucket_ns,
                plural="buckets",
                name=bucket_name,
            )

            bucket_spec = bucket_obj.get("spec", {})
            provider_ref = bucket_spec.get("providerRef", {})
            provider_name = provider_ref.get("name")

            if provider_name:
                provider_ns = provider_ref.get("namespace", bucket_ns)
                provider_obj = api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=provider_ns,
                    plural="providers",
                    name=provider_name,
                )

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Delete policy
                if provider_client.bucket_exists(bucket_name):
                    provider_client.delete_bucket_policy(bucket_name)
                    logger.info(f"Deleted policy from bucket {bucket_name}")
        except Exception as e:
            logger.error(f"Failed to delete policy from bucket {bucket_name}: {e}")
            # Don't fail deletion if cleanup fails


@kopf.on.create(API_GROUP_VERSION, KIND_ACCESS_KEY)
@kopf.on.update(API_GROUP_VERSION, KIND_ACCESS_KEY)
@kopf.on.resume(API_GROUP_VERSION, KIND_ACCESS_KEY)
def handle_access_key(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle AccessKey resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")

        if not provider_name:
            error_msg = "providerRef.name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Get provider
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        api = client.CustomObjectsApi()
        provider_ns = provider_ref.get("namespace", namespace)

        try:
            provider_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=provider_ns,
                plural="providers",
                name=provider_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                # Don't re-raise - return to stop retries until provider is created
                return
            raise

        # Check if provider is ready
        provider_status = provider_obj.get("status", {})
        provider_conditions = provider_status.get("conditions", [])
        provider_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in provider_conditions
        )

        if not provider_ready:
            error_msg = f"Provider {provider_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_provider_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            raise kopf.TemporaryError(error_msg)

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Check if userRef is provided
        user_ref = spec.get("userRef", {})
        user_name = user_ref.get("name")
        
        if not user_name:
            error_msg = "userRef.name is required for creating access keys"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
            raise ValueError(error_msg)

        # Get user
        try:
            user_ns = user_ref.get("namespace", namespace)
            user_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=user_ns,
                plural="users",
                name=user_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"User {user_name} not found in namespace {user_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                # Don't re-raise - return to stop retries until user is created
                return
            raise

        # Check if user is ready
        user_status = user_obj.get("status", {})
        user_conditions = user_status.get("conditions", [])
        user_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in user_conditions
        )

        if not user_ready:
            error_msg = f"User {user_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_provider_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            # Raise exception to trigger retry
            raise kopf.TemporaryError(error_msg)

        # Get the actual IAM user name from the User CRD spec
        user_spec = user_obj.get("spec", {})
        iam_user_name = user_spec.get("name")
        if not iam_user_name:
            error_msg = f"User {user_name} does not have a valid IAM user name in spec"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Check if access key already exists
        existing_key_id = status.get("accessKeyId")
        conditions = status.get("conditions", [])

        if not existing_key_id:
            # Create access key for the user via IAM
            try:
                key_response = provider_client.create_access_key(iam_user_name)
                access_key_id = key_response.get("AccessKey", {}).get("AccessKeyId")
                secret_access_key = key_response.get("AccessKey", {}).get("SecretAccessKey")

                # Create Kubernetes secret
                secret_name = f"{name}-credentials"
                try:
                    core_api = client.CoreV1Api()
                    create_access_key_secret(
                        core_api,
                        namespace,
                        secret_name,
                        access_key_id,
                        secret_access_key,
                        owner_references=[
                            {
                                "apiVersion": "s3.cloud37.dev/v1alpha1",
                                "kind": "AccessKey",
                                "name": name,
                                "uid": meta.get("uid"),
                                "controller": True,
                            }
                        ],
                    )
                    emit_access_key_created(meta, access_key_id)
                    logger.info(f"Created access key {access_key_id} for user {iam_user_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 409:
                        # Secret already exists, read it
                        logger.info(f"Secret {secret_name} already exists")
                    else:
                        raise

                # Set ready condition
                conditions = set_ready_condition(conditions, True, f"Access key {access_key_id} created for user {iam_user_name}")

                # Update status
                status_update = {
                    "observedGeneration": meta.get("generation", 0),
                    "accessKeyId": access_key_id,
                    "created": True,
                    "conditions": conditions,
                }

                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="success").inc()
                patch.status.update(status_update)
            except Exception as e:
                error_msg = f"Failed to create access key: {str(e)}"
                logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
        else:
            # Access key already exists
            logger.info(f"Access key {existing_key_id} already exists")
            conditions = set_ready_condition(conditions, True, f"Access key {existing_key_id} is ready")

            status_update = {
                "observedGeneration": meta.get("generation", 0),
                "accessKeyId": existing_key_id,
                "created": True,
                "conditions": conditions,
            }

            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="success").inc()
            patch.status.update(status_update)
    except Exception as e:
        logger.error(f"AccessKey reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_ACCESS_KEY).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_ACCESS_KEY)
def handle_access_key_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle AccessKey resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")

    logger.info(f"AccessKey {name} is being deleted")

    # Secret will be deleted via owner reference
    # In a real implementation, we would also revoke the key from the provider


@kopf.on.create(API_GROUP_VERSION, KIND_USER)
@kopf.on.update(API_GROUP_VERSION, KIND_USER)
@kopf.on.resume(API_GROUP_VERSION, KIND_USER)
def handle_user(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle User resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_USER, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")
        user_name = spec.get("name")

        if not provider_name:
            error_msg = "providerRef.name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
            raise ValueError(error_msg)

        if not user_name:
            error_msg = "user name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Get provider
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        api = client.CustomObjectsApi()
        provider_ns = provider_ref.get("namespace", namespace)

        try:
            provider_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=provider_ns,
                plural="providers",
                name=provider_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                # Don't re-raise - return to stop retries until provider is created
                return
            raise

        # Check if provider is ready
        provider_status = provider_obj.get("status", {})
        provider_conditions = provider_status.get("conditions", [])
        provider_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in provider_conditions
        )

        if not provider_ready:
            error_msg = f"Provider {provider_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_provider_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            raise kopf.TemporaryError(error_msg)

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Check if user already exists
        existing_user_id = status.get("userId")
        conditions = status.get("conditions", [])

        if not existing_user_id:
            # Create user in IAM
            try:
                policy = spec.get("policy")
                policy_ref = spec.get("policyRef")
                
                # Check for mutually exclusive policy options
                if policy and policy_ref:
                    error_msg = "Cannot specify both policy and policyRef"
                    logger.error(error_msg)
                    conditions = set_creation_failed_condition(conditions, error_msg)
                    emit_reconcile_failed(meta, error_msg)
                    metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                    patch.status.update({
                        "conditions": conditions,
                        "observedGeneration": meta.get("generation", 0),
                    })
                    return
                
                # If policyRef is provided, fetch the IAMPolicy
                if policy_ref:
                    policy_name = policy_ref.get("name")
                    policy_ns = policy_ref.get("namespace", namespace)
                    
                    if not policy_name:
                        error_msg = "policyRef.name is required"
                        logger.error(error_msg)
                        conditions = set_creation_failed_condition(conditions, error_msg)
                        emit_reconcile_failed(meta, error_msg)
                        metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                        patch.status.update({
                            "conditions": conditions,
                            "observedGeneration": meta.get("generation", 0),
                        })
                        return
                    
                    # Fetch the IAMPolicy
                    try:
                        policy_obj = api.get_namespaced_custom_object(
                            group="s3.cloud37.dev",
                            version="v1alpha1",
                            namespace=policy_ns,
                            plural="iampolicies",
                            name=policy_name,
                        )
                        
                        # Check if policy is ready
                        policy_status = policy_obj.get("status", {})
                        policy_conditions = policy_status.get("conditions", [])
                        policy_ready = any(
                            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in policy_conditions
                        )
                        
                        if not policy_ready:
                            error_msg = f"IAMPolicy {policy_name} is not ready"
                            logger.warning(error_msg)
                            conditions = set_creation_failed_condition(conditions, error_msg)
                            emit_reconcile_failed(meta, error_msg)
                            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                            patch.status.update({
                                "conditions": conditions,
                                "observedGeneration": meta.get("generation", 0),
                            })
                            raise kopf.TemporaryError(error_msg)
                        
                        # Mark that we'll attach the managed policy after user creation
                        # Don't fetch the policy yet - we'll attach the managed policy instead
                        logger.info(f"Will attach managed policy {policy_name} to user {user_name}")
                        policy = None  # Set to None to indicate we're using policyRef
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            error_msg = f"IAMPolicy {policy_name} not found in namespace {policy_ns}"
                            logger.error(error_msg)
                            conditions = set_creation_failed_condition(conditions, error_msg)
                            emit_reconcile_failed(meta, error_msg)
                            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                            patch.status.update({
                                "conditions": conditions,
                                "observedGeneration": meta.get("generation", 0),
                            })
                            return
                        raise
                
                # If no policy provided, create a default policy allowing access to the bucket
                if not policy:
                    # Try to get bucket name from tags or create a basic policy
                    tags = spec.get("tags", {})
                    bucket_name = tags.get("Bucket", user_name)
                    
                    policy = {
                        "version": "2012-10-17",
                        "statement": [
                            {
                                "effect": "Allow",
                                "action": ["s3:*"],
                                "resource": [
                                    f"arn:aws:s3:::{bucket_name}",
                                    f"arn:aws:s3:::{bucket_name}/*",
                                ],
                            }
                        ],
                    }
                    logger.info(f"No policy provided, creating default policy for bucket {bucket_name}")
                
                # Create user (with or without inline policy)
                if policy:
                    logger.info(f"Creating user {user_name} with inline policy: {policy}")
                    user_response = provider_client.create_user(user_name, policy)
                else:
                    logger.info(f"Creating user {user_name} without inline policy")
                    user_response = provider_client.create_user(user_name, None)
                
                user_id = user_response.get("User", {}).get("UserId")
                
                # If policyRef was specified, attach the managed policy
                if policy_ref and policy_name:
                    try:
                        logger.info(f"Attaching managed policy {policy_name} to user {user_name}")
                        provider_client.attach_managed_policy_to_user(user_name, policy_name)
                        logger.info(f"Successfully attached managed policy {policy_name} to user {user_name}")
                    except Exception as e:
                        error_msg = f"Failed to attach managed policy {policy_name}: {str(e)}"
                        logger.error(error_msg)
                        # Don't fail user creation if policy attachment fails
                
                conditions = set_ready_condition(conditions, True, f"User {user_name} created")
                logger.info(f"Created user {user_name} with ID {user_id}")
                
                # Update status on success
                status_update = {
                    "observedGeneration": meta.get("generation", 0),
                    "userId": user_id,
                    "created": True,
                    "lastSyncTime": datetime.now(timezone.utc).isoformat(),
                    "conditions": conditions,
                }

                metrics.reconcile_total.labels(kind=KIND_USER, result="success").inc()
                patch.status.update(status_update)
            except Exception as e:
                error_msg = f"Failed to create user: {str(e)}"
                logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
        else:
            # User already exists
            logger.info(f"User {user_name} already exists")
            conditions = set_ready_condition(conditions, True, f"User {user_name} is ready")

            status_update = {
                "observedGeneration": meta.get("generation", 0),
                "userId": existing_user_id,
                "created": True,
                "conditions": conditions,
            }

            metrics.reconcile_total.labels(kind=KIND_USER, result="success").inc()
            patch.status.update(status_update)
    except Exception as e:
        logger.error(f"User reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_USER, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_USER).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_USER)
def handle_user_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle User resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")
    user_name = spec.get("name")

    logger.info(f"User {name} is being deleted")

    if user_name:
        try:
            # Get provider
            provider_ref = spec.get("providerRef", {})
            provider_name = provider_ref.get("name")

            if provider_name:
                from kubernetes import client, config

                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()

                api = client.CustomObjectsApi()
                namespace = meta.get("namespace", "default")
                provider_ns = provider_ref.get("namespace", namespace)

                provider_obj = api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=provider_ns,
                    plural="providers",
                    name=provider_name,
                )

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Delete user
                provider_client.delete_user(user_name)
                logger.info(f"Deleted user {user_name}")
        except Exception as e:
            logger.error(f"Failed to delete user {user_name}: {e}")
            # Don't fail deletion if cleanup fails


@kopf.on.create(API_GROUP_VERSION, KIND_IAM_POLICY)
@kopf.on.update(API_GROUP_VERSION, KIND_IAM_POLICY)
@kopf.on.resume(API_GROUP_VERSION, KIND_IAM_POLICY)
def handle_iampolicy(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle IAMPolicy resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="started").inc()

    # Track duration
    start_time = time.time()
    try:
        # Validate spec
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")
        policy = spec.get("policy", {})

        if not provider_name:
            error_msg = "providerRef.name is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        if not policy:
            error_msg = "policy is required"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        # Validate policy document structure
        if not isinstance(policy, dict) or "statement" not in policy:
            error_msg = "policy must contain 'statement' field"
            emit_validate_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
            raise ValueError(error_msg)

        emit_validate_succeeded(meta)

        # Get provider
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        api = client.CustomObjectsApi()
        provider_ns = provider_ref.get("namespace", namespace)

        try:
            provider_obj = api.get_namespaced_custom_object(
                group="s3.cloud37.dev",
                version="v1alpha1",
                namespace=provider_ns,
                plural="providers",
                name=provider_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                return
            raise

        # Check if provider is ready
        provider_status = provider_obj.get("status", {})
        provider_conditions = provider_status.get("conditions", [])
        provider_ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True" for cond in provider_conditions
        )

        if not provider_ready:
            error_msg = f"Provider {provider_name} is not ready"
            logger.warning(error_msg)
            conditions = status.get("conditions", [])
            conditions = set_provider_not_ready_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            raise kopf.TemporaryError(error_msg)

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Convert policy to AWS format
        import json
        from .services.aws.client import AWSProvider
        
        if isinstance(provider_client, AWSProvider):
            aws_policy = provider_client._convert_policy_to_aws_format(policy)
        else:
            aws_policy = policy

        # Create managed policy in Wasabi
        conditions = status.get("conditions", [])
        policy_arn = None
        
        try:
            # Create or get the managed policy
            tags = spec.get("tags", {})
            description = f"IAMPolicy {name} managed by wasabi-s3-operator"
            
            policy_response = provider_client.create_managed_policy(
                policy_name=name,
                policy_document=aws_policy,
                description=description
            )
            
            # Extract policy ARN from response
            policy_arn = policy_response.get("Policy", {}).get("Arn")
            if not policy_arn:
                policy_arn = f"arn:aws:iam::*:policy/{name}"
            
            logger.info(f"Created managed policy {name} with ARN {policy_arn}")
            conditions = set_ready_condition(conditions, True, f"IAMPolicy {name} is ready")
            
        except Exception as e:
            error_msg = f"Failed to create managed policy: {str(e)}"
            logger.error(error_msg)
            conditions = set_attach_failed_condition(conditions, error_msg)
            emit_reconcile_failed(meta, error_msg)
            metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
            patch.status.update({
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            })
            raise

        # Update status
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "applied": True,
            "policyArn": policy_arn,
            "attachedUsers": [],  # Will be populated when users reference this policy
            "lastSyncTime": datetime.now(timezone.utc).isoformat(),
            "conditions": conditions,
        }

        metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="success").inc()
        patch.status.update(status_update)
    except Exception as e:
        logger.error(f"IAMPolicy reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_IAM_POLICY).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_IAM_POLICY)
def handle_iampolicy_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    **kwargs: Any,
) -> None:
    """Handle IAMPolicy resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")
    namespace = meta.get("namespace", "default")

    logger.info(f"IAMPolicy {name} is being deleted")
    
    # Delete the managed policy from Wasabi
    try:
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")
        
        if provider_name:
            from kubernetes import client, config
            
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            
            api = client.CustomObjectsApi()
            provider_ns = provider_ref.get("namespace", namespace)
            
            try:
                provider_obj = api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=provider_ns,
                    plural="providers",
                    name=provider_name,
                )
                
                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))
                
                # Delete the managed policy
                provider_client.delete_managed_policy(name)
                logger.info(f"Deleted managed policy {name} from Wasabi")
            except Exception as e:
                logger.error(f"Failed to delete managed policy {name}: {e}")
                # Don't fail deletion if cleanup fails
    except Exception as e:
        logger.error(f"Failed to delete IAMPolicy {name}: {e}")
        # Don't fail deletion if cleanup fails

