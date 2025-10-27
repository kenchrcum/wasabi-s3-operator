"""Main entry point for the S3 Operator."""

from __future__ import annotations

import os
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
    KIND_PROVIDER,
)
from .utils.conditions import (
    set_apply_failed_condition,
    set_bucket_not_ready_condition,
    set_creation_failed_condition,
    set_policy_invalid_condition,
    set_provider_not_ready_condition,
    set_rotation_failed_condition,
    set_auth_valid_condition,
    set_endpoint_reachable_condition,
    set_ready_condition,
)
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
    try:
        settings.persistence.progress_storage = kopf.StatusProgressStorage()
        settings.persistence.diffbase_storage = kopf.AnnotationDiffBaseStorage()
    except Exception:
        settings.persistence.progress_storage = kopf.SmartProgressStorage()

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
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle Provider resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")
    uid = meta.get("uid", "unknown")

    emit_reconcile_started(meta)

    # Track reconciliation
    metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="started").inc()

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
            except Exception as e:
                connected = False
                endpoint_message = f"Connectivity test failed: {str(e)}"
                logger.error(f"Connectivity test failed for {name}: {e}")
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

        return status_update

    except Exception as e:
        logger.error(f"Provider reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="error").inc()
        raise


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
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle Bucket resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_BUCKET, result="started").inc()

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
                error_msg = f"Provider {provider_name} not found"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
                return {
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                }
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
            return {
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            }

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
            except Exception as e:
                error_msg = f"Failed to create bucket: {str(e)}"
                logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
                return {
                    "exists": False,
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                }
        else:
            emit_bucket_updated(meta, bucket_name)
            logger.info(f"Bucket {bucket_name} already exists")

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

        metrics.reconcile_total.labels(kind=KIND_BUCKET, result="success").inc()
        return status_update

    except Exception as e:
        logger.error(f"Bucket reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_BUCKET, result="error").inc()
        raise


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
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle BucketPolicy resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="started").inc()

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
                error_msg = f"Bucket {bucket_name} not found"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_bucket_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                return {
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                }
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
            return {
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            }

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
            return {
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            }

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
                return {
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                }

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
            return {
                "applied": False,
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            }

        # Update status
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "applied": True,
            "lastSyncTime": datetime.now(timezone.utc).isoformat(),
            "conditions": conditions,
        }

        metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="success").inc()
        return status_update

    except Exception as e:
        logger.error(f"BucketPolicy reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="error").inc()
        raise


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
    **kwargs: Any,
) -> dict[str, Any]:
    """Handle AccessKey resource reconciliation."""
    import logging

    logger = logging.getLogger(__name__)
    namespace = meta.get("namespace", "default")
    name = meta.get("name", "unknown")

    emit_reconcile_started(meta)
    metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="started").inc()

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
                error_msg = f"Provider {provider_name} not found"
                logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                return {
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                }
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
            return {
                "conditions": conditions,
                "observedGeneration": meta.get("generation", 0),
            }

        # Create provider client
        provider_spec = provider_obj.get("spec", {})
        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

        # Check if access key already exists
        existing_key_id = status.get("accessKeyId")
        conditions = status.get("conditions", [])

        if not existing_key_id:
            # Generate new access key
            # Note: In a real implementation, this would call the provider's API
            # For now, we'll simulate key generation
            from ..utils.access_keys import (
                create_access_key_secret,
                generate_access_key_id,
                generate_secret_access_key,
            )

            access_key_id = generate_access_key_id()
            secret_access_key = generate_secret_access_key()

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
                logger.info(f"Created access key {access_key_id}")
            except client.exceptions.ApiException as e:
                if e.status == 409:
                    # Secret already exists, read it
                    logger.info(f"Secret {secret_name} already exists")
                else:
                    raise

            # Set ready condition
            conditions = set_ready_condition(conditions, True, f"Access key {access_key_id} created")

            # Update status
            status_update = {
                "observedGeneration": meta.get("generation", 0),
                "accessKeyId": access_key_id,
                "created": True,
                "conditions": conditions,
            }

            metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="success").inc()
            return status_update
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
            return status_update

    except Exception as e:
        logger.error(f"AccessKey reconciliation failed for {name}: {e}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {str(e)}")
        metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="error").inc()
        raise


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

