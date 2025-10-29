"""Main entry point for the Wasabi S3 Operator Operator."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import kopf
from prometheus_client import make_wsgi_app
from werkzeug.serving import make_server
import threading

from . import health
from . import logging as structured_logging
from . import metrics
from .utils.errors import sanitize_exception
from .builders.bucket import create_bucket_config_from_spec
from .builders.provider import create_provider_from_spec
from .constants import (
    API_GROUP_VERSION,
    FINALIZER,
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
from .utils.access_keys import create_access_key_secret, update_access_key_secret
from .utils.cache import get_cached_object, make_cache_key, set_cached_object
from .utils.rate_limit import handle_rate_limit_error, rate_limit_k8s
from .utils.secrets import (
    cleanup_expired_previous_secrets,
    create_previous_secret,
    read_secret_data,
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


def ensure_finalizer(meta: dict[str, Any], patch: kopf.Patch) -> None:
    """Ensure finalizer is present in metadata."""
    finalizers = meta.get("finalizers", [])
    if FINALIZER not in finalizers:
        finalizers.append(FINALIZER)
        patch.metadata["finalizers"] = finalizers


def remove_finalizer(meta: dict[str, Any], patch: kopf.Patch) -> None:
    """Remove finalizer from metadata."""
    finalizers = meta.get("finalizers", [])
    if FINALIZER in finalizers:
        finalizers.remove(FINALIZER)
        patch.metadata["finalizers"] = finalizers if finalizers else None


def get_provider_with_cache(
    api: Any,
    provider_name: str,
    provider_ns: str,
    namespace: str = "default",
) -> dict[str, Any]:
    """Get provider CRD with caching.
    
    Args:
        api: Kubernetes CustomObjectsApi instance
        provider_name: Name of the provider
        provider_ns: Namespace of the provider
        namespace: Current namespace (for fallback)
        
    Returns:
        Provider CRD object
        
    Raises:
        client.exceptions.ApiException: If provider not found or API error
    """
    cache_key = make_cache_key(KIND_PROVIDER, provider_ns, provider_name)
    cached_provider = get_cached_object(cache_key)
    
    if cached_provider is not None:
        metrics.api_call_total.labels(api_type="k8s", operation="get_provider", result="cache_hit").inc()
        return cached_provider
    
    start_time = time.time()
    try:
        provider_obj = rate_limit_k8s(api.get_namespaced_custom_object)(
            group="s3.cloud37.dev",
            version="v1alpha1",
            namespace=provider_ns,
            plural="providers",
            name=provider_name,
        )
        metrics.api_call_total.labels(api_type="k8s", operation="get_provider", result="success").inc()
        set_cached_object(cache_key, provider_obj)
        return provider_obj
    except Exception as e:
        metrics.api_call_total.labels(api_type="k8s", operation="get_provider", result="error").inc()
        if handle_rate_limit_error(e):
            # Retry once after rate limit backoff
            return get_provider_with_cache(api, provider_name, provider_ns, namespace)
        raise
    finally:
        duration = time.time() - start_time
        metrics.api_call_duration_seconds.labels(api_type="k8s", operation="get_provider").observe(duration)


def get_user_with_cache(
    api: Any,
    user_name: str,
    user_ns: str,
) -> dict[str, Any]:
    """Get user CRD with caching.
    
    Args:
        api: Kubernetes CustomObjectsApi instance
        user_name: Name of the user
        user_ns: Namespace of the user
        
    Returns:
        User CRD object
        
    Raises:
        client.exceptions.ApiException: If user not found or API error
    """
    cache_key = make_cache_key(KIND_USER, user_ns, user_name)
    cached_user = get_cached_object(cache_key)
    
    if cached_user is not None:
        metrics.api_call_total.labels(api_type="k8s", operation="get_user", result="cache_hit").inc()
        return cached_user
    
    start_time = time.time()
    try:
        user_obj = rate_limit_k8s(api.get_namespaced_custom_object)(
            group="s3.cloud37.dev",
            version="v1alpha1",
            namespace=user_ns,
            plural="users",
            name=user_name,
        )
        metrics.api_call_total.labels(api_type="k8s", operation="get_user", result="success").inc()
        set_cached_object(cache_key, user_obj)
        return user_obj
    except Exception as e:
        metrics.api_call_total.labels(api_type="k8s", operation="get_user", result="error").inc()
        if handle_rate_limit_error(e):
            # Retry once after rate limit backoff
            return get_user_with_cache(api, user_name, user_ns)
        raise
    finally:
        duration = time.time() - start_time
        metrics.api_call_duration_seconds.labels(api_type="k8s", operation="get_user").observe(duration)


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

    # Configure retry/backoff settings
    # Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (max)
    settings.execution.min_retry_delay = 1.0
    settings.execution.max_retry_delay = 60.0
    settings.execution.retry_backoff = 2.0  # Exponential multiplier
    settings.execution.max_retries = 5  # Maximum retry attempts
    settings.execution.backoff_jitter = 0.1  # 10% jitter to prevent thundering herd

    # Start metrics HTTP server with health check endpoints on port 8080
    metrics_port = int(os.getenv("METRICS_PORT", "8080"))
    combined_app = health.create_combined_wsgi_app()
    server = make_server("", metrics_port, combined_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


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

    # Ensure finalizer is present
    ensure_finalizer(meta, patch)

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
            sanitized_error = sanitize_exception(e)
            auth_message = f"Authentication failed: {sanitized_error}"
            error_type = type(e).__name__
            metrics.error_total.labels(kind=KIND_PROVIDER, error_type=error_type).inc()
            logger.error(f"Failed to create provider for {name}: {sanitized_error}")

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
                sanitized_error = sanitize_exception(e)
                endpoint_message = f"Connectivity test failed: {sanitized_error}"
                error_type = type(e).__name__
                metrics.error_total.labels(kind=KIND_PROVIDER, error_type=error_type).inc()
                logger.error(f"Connectivity test failed for {name}: {sanitized_error}")
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
            metrics.resource_status_total.labels(kind=KIND_PROVIDER, status="ready").inc()
        else:
            metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="failed").inc()
            metrics.resource_status_total.labels(kind=KIND_PROVIDER, status="not_ready").inc()

        # Update status directly via patch to avoid conflicts
        patch.status.update(status_update)
    except Exception as e:
        sanitized_error = sanitize_exception(e)
        error_type = type(e).__name__
        metrics.error_total.labels(kind=KIND_PROVIDER, error_type=error_type).inc()
        logger.error(f"Provider reconciliation failed for {name}: {sanitized_error}")
        emit_reconcile_failed(meta, f"Reconciliation failed: {sanitized_error}")
        metrics.reconcile_total.labels(kind=KIND_PROVIDER, result="error").inc()
        metrics.resource_status_total.labels(kind=KIND_PROVIDER, status="error").inc()
        raise
    finally:
        # Record duration
        duration = time.time() - start_time
        metrics.reconcile_duration_seconds.labels(kind=KIND_PROVIDER).observe(duration)


@kopf.on.delete(API_GROUP_VERSION, KIND_PROVIDER)
def handle_provider_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle Provider resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")

    logger.info(f"Provider {name} is being deleted")
    # No cleanup needed for provider itself
    
    # Remove finalizer to allow deletion
    remove_finalizer(meta, patch)


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET)
@kopf.timer(API_GROUP_VERSION, KIND_BUCKET, interval=int(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "300")))  # Default 5 minutes
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
    
    # Ensure finalizer is present
    ensure_finalizer(meta, patch)
    
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
            provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
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
            # Bucket exists - reconcile configuration changes
            logger.info(f"Bucket {bucket_name} already exists, checking for configuration drift")
            
            drift_detected = False
            try:
                # Check versioning configuration
                current_versioning = provider_client.get_bucket_versioning(bucket_name)
                desired_versioning_enabled = bucket_config.get("versioning_enabled", False)
                desired_mfa_delete = bucket_config.get("mfa_delete", False)
                
                if current_versioning.get("enabled") != desired_versioning_enabled or \
                   current_versioning.get("mfa_delete") != desired_mfa_delete:
                    drift_detected = True
                    logger.info(f"Drift detected: versioning configuration for bucket {bucket_name}")
                    metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="versioning").inc()
                    provider_client.set_bucket_versioning(bucket_name, desired_versioning_enabled, desired_mfa_delete)
                    metrics.bucket_operations_total.labels(operation="update_versioning", result="success").inc()
                
                # Check encryption configuration
                current_encryption = provider_client.get_bucket_encryption(bucket_name)
                desired_encryption_enabled = bucket_config.get("encryption_enabled", False)
                desired_algorithm = bucket_config.get("encryption_algorithm", "AES256")
                desired_kms_key_id = bucket_config.get("kms_key_id")
                
                current_algorithm = current_encryption.get("algorithm")
                current_kms_key_id = current_encryption.get("kms_key_id")
                
                if desired_encryption_enabled:
                    if current_algorithm != desired_algorithm or current_kms_key_id != desired_kms_key_id:
                        drift_detected = True
                        logger.info(f"Drift detected: encryption configuration for bucket {bucket_name}")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="encryption").inc()
                        try:
                            provider_client.set_bucket_encryption(bucket_name, desired_algorithm, desired_kms_key_id)
                            metrics.bucket_operations_total.labels(operation="update_encryption", result="success").inc()
                        except Exception as e:
                            logger.warning(f"Failed to update encryption for bucket {bucket_name}: {e}")
                            metrics.bucket_operations_total.labels(operation="update_encryption", result="failed").inc()
                elif current_algorithm is not None:
                    # Encryption should be disabled but is currently enabled
                    # Note: AWS doesn't support disabling encryption, but we log it
                    drift_detected = True
                    logger.info(f"Drift detected: encryption is enabled on bucket {bucket_name} but desired state is disabled")
                    metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="encryption").inc()
                
                # Check tags configuration
                desired_tags = bucket_config.get("tags") or {}
                if desired_tags:
                    current_tags = provider_client.get_bucket_tags(bucket_name)
                    if current_tags != desired_tags:
                        drift_detected = True
                        logger.info(f"Drift detected: tags configuration for bucket {bucket_name}")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="tags").inc()
                        provider_client.set_bucket_tags(bucket_name, desired_tags)
                        metrics.bucket_operations_total.labels(operation="update_tags", result="success").inc()
                
                emit_bucket_updated(meta, bucket_name)
                logger.info(f"Bucket {bucket_name} configuration reconciled")
                metrics.bucket_operations_total.labels(operation="reconcile", result="success").inc()
            except Exception as e:
                logger.warning(f"Failed to reconcile bucket configuration for {bucket_name}: {e}")
                # Don't fail reconciliation, just log the warning
                metrics.bucket_operations_total.labels(operation="reconcile", result="failed").inc()

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
                        # Configurable via USER_READINESS_TIMEOUT_SECONDS environment variable
                        max_wait_time = int(os.getenv("USER_READINESS_TIMEOUT_SECONDS", "60"))
                        wait_interval = 1  # Check every second
                        elapsed_time = 0
                        
                        while not user_ready and elapsed_time < max_wait_time:
                            time.sleep(wait_interval)
                            elapsed_time += wait_interval
                            
                            try:
                                user_obj = get_user_with_cache(api, user_crd_name, namespace)
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
    patch: kopf.Patch,
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
            # Get deletion policy (default to Retain for safety)
            deletion_policy = spec.get("deletionPolicy", "Retain")
            force_delete = spec.get("forceDelete", False)
            
            logger.info(f"Deletion policy for bucket {bucket_name}: {deletion_policy}, forceDelete: {force_delete}")

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

                provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Handle deletion based on policy
                if provider_client.bucket_exists(bucket_name):
                    if deletion_policy == "Delete":
                        # Delete the bucket (will empty it if forceDelete is True)
                        provider_client.delete_bucket(bucket_name, force=force_delete)
                        emit_bucket_deleted(meta, bucket_name)
                        logger.info(f"Deleted bucket {bucket_name}")
                    else:
                        # Retain policy - just log that we're keeping the bucket
                        logger.info(f"Retaining bucket {bucket_name} per deletionPolicy=Retain")
                        emit_bucket_deleted(meta, bucket_name)
                else:
                    logger.info(f"Bucket {bucket_name} does not exist, skipping deletion")
        except Exception as e:
            logger.error(f"Failed to delete bucket {bucket_name}: {e}")
            # Don't fail deletion if cleanup fails
        finally:
            # Remove finalizer to allow deletion
            remove_finalizer(meta, patch)


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.timer(API_GROUP_VERSION, KIND_BUCKET_POLICY, interval=int(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "300")))  # Default 5 minutes
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
    
    # Ensure finalizer is present
    ensure_finalizer(meta, patch)
    
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
        provider_obj = get_provider_with_cache(api, provider_name, provider_ns, bucket_ns)

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
                return

            # Check if policy has changed by comparing with current policy
            policy_changed = True
            try:
                current_policy = provider_client.get_bucket_policy(bucket_name)
                if current_policy is not None:
                    # Convert current policy to CRD format for comparison
                    from .services.aws.client import AWSProvider
                    if isinstance(provider_client, AWSProvider):
                        # Normalize both policies for comparison
                        import json
                        current_policy_normalized = json.dumps(current_policy, sort_keys=True)
                        desired_policy_normalized = json.dumps(
                            provider_client._convert_policy_to_aws_format(policy),
                            sort_keys=True
                        )
                        policy_changed = current_policy_normalized != desired_policy_normalized
                        
                        if not policy_changed:
                            logger.info(f"Policy for bucket {bucket_name} unchanged, skipping update")
                        else:
                            # Drift detected in policy
                            logger.info(f"Drift detected: policy for bucket {bucket_name}")
                            metrics.drift_detected_total.labels(kind=KIND_BUCKET_POLICY, resource_type="policy").inc()
                else:
                    # No policy exists, needs to be set
                    logger.info(f"No existing policy for bucket {bucket_name}, will create new policy")
                    policy_changed = True
            except Exception as e:
                # If we can't get current policy, assume it needs to be set
                # This handles unexpected errors
                logger.debug(f"Could not get current policy for comparison: {e}")
                policy_changed = True

            # Apply policy only if it changed
            if policy_changed:
                provider_client.set_bucket_policy(bucket_name, policy)
                emit_policy_applied(meta, bucket_name)
                logger.info(f"Applied policy to bucket {bucket_name}")
            else:
                logger.info(f"Policy for bucket {bucket_name} is already up to date")

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
    patch: kopf.Patch,
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
                provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Delete policy
                if provider_client.bucket_exists(bucket_name):
                    provider_client.delete_bucket_policy(bucket_name)
                    logger.info(f"Deleted policy from bucket {bucket_name}")
        except Exception as e:
            logger.error(f"Failed to delete policy from bucket {bucket_name}: {e}")
            # Don't fail deletion if cleanup fails
        finally:
            # Remove finalizer to allow deletion
            remove_finalizer(meta, patch)


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
    
    # Ensure finalizer is present
    ensure_finalizer(meta, patch)
    
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
            user_obj = get_user_with_cache(api, user_name, user_ns)
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
        annotations = meta.get("annotations", {})

        # Check if rotation is enabled and needed
        rotate_config = spec.get("rotate", {})
        rotation_enabled = rotate_config.get("enabled", False)
        rotation_interval_days = rotate_config.get("intervalDays", 90)
        retention_days = rotate_config.get("previousKeysRetentionDays", 7)

        needs_rotation = False
        if rotation_enabled and existing_key_id:
            # Check if we need to rotate
            next_rotate_time_str = status.get("nextRotateTime")
            if next_rotate_time_str:
                from datetime import datetime
                try:
                    next_rotate_time = datetime.fromisoformat(next_rotate_time_str.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    if now >= next_rotate_time:
                        needs_rotation = True
                        logger.info(f"Access key {existing_key_id} needs rotation")
                except Exception as e:
                    logger.warning(f"Failed to parse nextRotateTime: {e}")

        if not existing_key_id:
            # Create initial access key for the user via IAM
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

                # Calculate next rotation time if rotation is enabled
                last_rotate_time = datetime.now(timezone.utc).isoformat()
                next_rotate_time = None
                if rotation_enabled:
                    from datetime import timedelta
                    next_rotate_time = (datetime.now(timezone.utc) + timedelta(days=rotation_interval_days)).isoformat()

                # Set ready condition
                conditions = set_ready_condition(conditions, True, f"Access key {access_key_id} created for user {iam_user_name}")

                # Update status
                status_update = {
                    "observedGeneration": meta.get("generation", 0),
                    "accessKeyId": access_key_id,
                    "created": True,
                    "conditions": conditions,
                }
                
                if rotation_enabled:
                    status_update["lastRotateTime"] = last_rotate_time
                    status_update["nextRotateTime"] = next_rotate_time

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
        elif needs_rotation:
            # Rotate the access key
            try:
                logger.info(f"Rotating access key {existing_key_id} for user {iam_user_name}")
                
                # Read current secret to get old credentials
                secret_name = f"{name}-credentials"
                core_api = client.CoreV1Api()
                try:
                    old_secret_data = read_secret_data(core_api, namespace, secret_name)
                    old_access_key_id = old_secret_data.get("access-key-id")
                    old_secret_access_key = old_secret_data.get("secret-access-key")
                    
                    if not old_access_key_id or not old_secret_access_key:
                        raise ValueError("Current secret missing access key credentials")
                except ValueError as e:
                    logger.error(f"Failed to read current secret: {e}")
                    raise
                
                # Create new access key
                key_response = provider_client.create_access_key(iam_user_name)
                new_access_key_id = key_response.get("AccessKey", {}).get("AccessKeyId")
                new_secret_access_key = key_response.get("AccessKey", {}).get("SecretAccessKey")
                
                logger.info(f"Created new access key {new_access_key_id}")
                
                # Create previous secret with old credentials
                rotated_at = datetime.now(timezone.utc).isoformat()
                # Use timestamp to make secret name unique (format: YYYYMMDDHHMMSS)
                timestamp_str = rotated_at.replace("-", "").replace(":", "").replace(".", "").split("+")[0].split("T")
                timestamp_str = "".join(timestamp_str)[:14]  # Format: YYYYMMDDHHMMSS
                previous_secret_name = f"{name}-credentials-previous-{timestamp_str}"
                
                create_previous_secret(
                    core_api,
                    namespace,
                    previous_secret_name,
                    old_access_key_id,
                    old_secret_access_key,
                    rotated_at,
                    name,
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
                logger.info(f"Created previous secret {previous_secret_name} with old credentials")
                
                # Update the main secret with new credentials
                update_access_key_secret(
                    core_api,
                    namespace,
                    secret_name,
                    new_access_key_id,
                    new_secret_access_key,
                )
                logger.info(f"Updated secret {secret_name} with new credentials")
                
                # Calculate next rotation time
                from datetime import timedelta
                last_rotate_time = rotated_at
                next_rotate_time = (datetime.now(timezone.utc) + timedelta(days=rotation_interval_days)).isoformat()
                
                # Emit rotation event
                emit_access_key_rotated(meta, new_access_key_id)
                
                # Set ready condition
                conditions = set_ready_condition(conditions, True, f"Access key rotated to {new_access_key_id}")
                
                # Update status
                status_update = {
                    "observedGeneration": meta.get("generation", 0),
                    "accessKeyId": new_access_key_id,
                    "created": True,
                    "lastRotateTime": last_rotate_time,
                    "nextRotateTime": next_rotate_time,
                    "conditions": conditions,
                }
                
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="success").inc()
                patch.status.update(status_update)
                
            except Exception as e:
                error_msg = f"Failed to rotate access key: {str(e)}"
                logger.error(error_msg)
                conditions = set_rotation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
        else:
            # Access key already exists, no rotation needed
            logger.info(f"Access key {existing_key_id} already exists")
            
            # Check if we need to cleanup expired previous secrets and access keys
            if rotation_enabled:
                core_api = client.CoreV1Api()
                try:
                    # List expired previous secrets
                    from .utils.secrets import list_previous_secrets
                    expired_secrets = list_previous_secrets(
                        core_api,
                        namespace,
                        name,
                        include_expired=True,
                        retention_days=retention_days,
                    )
                    expired_secrets = [s for s in expired_secrets if s.get("is_expired", False)]
                    
                    # Delete expired access keys from Wasabi first (before deleting secrets)
                    for secret_info in expired_secrets:
                        try:
                            # Read the secret to get the access key ID
                            secret_data = read_secret_data(core_api, namespace, secret_info["name"])
                            expired_key_id = secret_data.get("access-key-id")
                            
                            if expired_key_id:
                                # Delete from Wasabi
                                try:
                                    provider_client.delete_access_key(iam_user_name, expired_key_id)
                                    logger.info(f"Deleted expired access key {expired_key_id} from Wasabi")
                                except Exception as e:
                                    logger.warning(f"Failed to delete expired access key {expired_key_id} from Wasabi: {e}")
                        except Exception as e:
                            logger.warning(f"Failed to read secret {secret_info['name']} for cleanup: {e}")
                    
                    # Now cleanup expired previous secrets from Kubernetes
                    deleted_secrets = cleanup_expired_previous_secrets(
                        core_api,
                        namespace,
                        name,
                        retention_days,
                    )
                    
                    if deleted_secrets:
                        logger.info(f"Deleted {len(deleted_secrets)} expired previous secrets: {deleted_secrets}")
                            
                except Exception as e:
                    logger.warning(f"Failed to cleanup expired previous secrets: {e}")
            
            conditions = set_ready_condition(conditions, True, f"Access key {existing_key_id} is ready")

            status_update = {
                "observedGeneration": meta.get("generation", 0),
                "accessKeyId": existing_key_id,
                "created": True,
                "conditions": conditions,
            }
            
            # Preserve rotation times if they exist
            if rotation_enabled:
                if status.get("lastRotateTime"):
                    status_update["lastRotateTime"] = status.get("lastRotateTime")
                if status.get("nextRotateTime"):
                    status_update["nextRotateTime"] = status.get("nextRotateTime")

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
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle AccessKey resource deletion."""
    import logging

    logger = logging.getLogger(__name__)
    name = meta.get("name", "unknown")
    namespace = meta.get("namespace", "default")

    logger.info(f"AccessKey {name} is being deleted")

    # Get access key ID from status
    access_key_id = status.get("accessKeyId")
    
    if access_key_id:
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
                provider_ns = provider_ref.get("namespace", namespace)
                
                try:
                    provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
                    
                    provider_spec = provider_obj.get("spec", {})
                    provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))
                    
                    # Get user name from userRef
                    user_ref = spec.get("userRef", {})
                    user_name = user_ref.get("name")
                    
                    if user_name:
                        # Get the actual IAM user name from the User CRD
                        user_ns = user_ref.get("namespace", namespace)
                        try:
                            user_obj = get_user_with_cache(api, user_name, user_ns)
                            user_spec = user_obj.get("spec", {})
                            iam_user_name = user_spec.get("name")
                            
                            if iam_user_name:
                                # Delete access key from Wasabi IAM
                                provider_client.delete_access_key(iam_user_name, access_key_id)
                                logger.info(f"Deleted access key {access_key_id} for user {iam_user_name}")
                            else:
                                logger.warning(f"User {user_name} does not have IAM user name in spec")
                        except client.exceptions.ApiException as e:
                            if e.status == 404:
                                logger.warning(f"User {user_name} not found, cannot delete access key")
                            else:
                                raise
                    else:
                        logger.warning(f"AccessKey {name} does not have userRef, cannot delete access key")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(f"Provider {provider_name} not found, cannot delete access key")
                    else:
                        raise
        except Exception as e:
            logger.error(f"Failed to delete access key {access_key_id}: {e}")
            # Don't fail deletion if cleanup fails - we'll still remove the finalizer
    
    # Secret will be deleted via owner reference
    
    # Remove finalizer to allow deletion
    remove_finalizer(meta, patch)


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
    
    # Ensure finalizer is present
    ensure_finalizer(meta, patch)
    
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
    patch: kopf.Patch,
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

                provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)

                provider_spec = provider_obj.get("spec", {})
                provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                # Delete user
                provider_client.delete_user(user_name)
                logger.info(f"Deleted user {user_name}")
        except Exception as e:
            logger.error(f"Failed to delete user {user_name}: {e}")
            # Don't fail deletion if cleanup fails
        finally:
            # Remove finalizer to allow deletion
            remove_finalizer(meta, patch)


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
    
    # Ensure finalizer is present
    ensure_finalizer(meta, patch)
    
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
    patch: kopf.Patch,
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
    finally:
        # Remove finalizer to allow deletion
        remove_finalizer(meta, patch)

