"""Handler for Bucket CRD."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import kopf
from kubernetes import client

from .. import metrics
from ..builders.bucket import create_bucket_config_from_spec
from ..builders.provider import create_provider_from_spec
from ..constants import API_GROUP_VERSION, KIND_BUCKET
from ..handlers.shared import get_provider_with_cache, get_user_with_cache, get_k8s_client
from ..tracing import trace_span
from ..utils.conditions import (
    set_creation_failed_condition,
    set_provider_not_ready_condition,
    set_ready_condition,
)
from ..utils.errors import sanitize_exception
from ..utils.events import (
    emit_bucket_created,
    emit_bucket_deleted,
    emit_bucket_updated,
    emit_validate_succeeded,
)
from .base import BaseHandler


class BucketHandler(BaseHandler):
    """Handler for Bucket resources."""

    def __init__(self):
        """Initialize bucket handler."""
        super().__init__(KIND_BUCKET)

    def reconcile(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Reconcile Bucket resource."""
        namespace = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        bucket_name = spec.get("name")
        provider_ref = spec.get("providerRef", {})

        with trace_span("reconcile_bucket", kind=KIND_BUCKET, attributes={"bucket.name": bucket_name or name}):
            # Validate spec
            if not bucket_name:
                self.handle_validation_error(meta, "bucket name is required")

            provider_name = provider_ref.get("name")
            if not provider_name:
                self.handle_validation_error(meta, "providerRef.name is required")

            emit_validate_succeeded(meta)

            # Get provider
            api = get_k8s_client()
            provider_ns = provider_ref.get("namespace", namespace)

            try:
                provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
                    self.handle_provider_not_found(meta, status, patch, provider_name, provider_ns, error_msg)
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
                self.handle_provider_not_ready(meta, status, patch, provider_name, error_msg)

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
                with trace_span("create_bucket", kind=KIND_BUCKET):
                    try:
                        provider_client.create_bucket(bucket_name, bucket_config)
                        emit_bucket_created(meta, bucket_name)
                        self.log_info(meta, f"Created bucket {bucket_name}", reason="BucketCreated", bucket_name=bucket_name)
                        metrics.bucket_operations_total.labels(operation="create", result="success").inc()
                    except Exception as e:
                        error_msg = f"Failed to create bucket: {str(e)}"
                        self.log_error(meta, error_msg, error=e, reason="CreationFailed", bucket_name=bucket_name)
                        conditions = set_creation_failed_condition(conditions, error_msg)
                        metrics.bucket_operations_total.labels(operation="create", result="failed").inc()
                        patch.status.update({
                            "exists": False,
                            "conditions": conditions,
                            "observedGeneration": meta.get("generation", 0),
                        })
            else:
                # Bucket exists - reconcile configuration changes
                self.log_info(meta, f"Bucket {bucket_name} already exists, checking for configuration drift", 
                             reason="DriftCheck", bucket_name=bucket_name)
                self._reconcile_bucket_configuration(provider_client, bucket_name, bucket_config, meta)

            # Handle auto-management if enabled
            auto_manage = spec.get("autoManage", {})
            auto_manage_enabled = auto_manage.get("enabled", True)
            accesskey_crd_name = None

            if auto_manage_enabled:
                with trace_span("auto_manage_bucket", kind=KIND_BUCKET):
                    accesskey_crd_name = self._handle_auto_management(
                        api, namespace, name, bucket_name, provider_name, provider_ns, auto_manage, meta
                    )

            # Set ready condition
            conditions = set_ready_condition(conditions, True, f"Bucket {bucket_name} is ready")

            # Update status
            status_data = {
                "bucketName": bucket_name,
                "exists": True,
                "lastSyncTime": datetime.now(timezone.utc).isoformat(),
                "conditions": conditions,
            }

            # Add credentials secret reference if auto-management is enabled
            if auto_manage_enabled and accesskey_crd_name:
                status_data["credentialsSecret"] = f"{accesskey_crd_name}-credentials"

            self.update_resource_status(patch, meta, True, status_data)

    def _reconcile_bucket_configuration(
        self,
        provider_client: Any,
        bucket_name: str,
        bucket_config: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        """Reconcile bucket configuration for drift detection."""
        try:
            # Check versioning configuration
            current_versioning = provider_client.get_bucket_versioning(bucket_name)
            desired_versioning_enabled = bucket_config.get("versioning_enabled", False)
            desired_mfa_delete = bucket_config.get("mfa_delete", False)

            if current_versioning.get("enabled") != desired_versioning_enabled or \
               current_versioning.get("mfa_delete") != desired_mfa_delete:
                self.log_info(meta, f"Drift detected: versioning configuration for bucket {bucket_name}",
                             reason="DriftDetected", bucket_name=bucket_name, resource_type="versioning")
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
                    self.log_info(meta, f"Drift detected: encryption configuration for bucket {bucket_name}",
                                 reason="DriftDetected", bucket_name=bucket_name, resource_type="encryption")
                    metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="encryption").inc()
                    try:
                        provider_client.set_bucket_encryption(bucket_name, desired_algorithm, desired_kms_key_id)
                        metrics.bucket_operations_total.labels(operation="update_encryption", result="success").inc()
                    except Exception as e:
                        self.log_warning(meta, f"Failed to update encryption for bucket {bucket_name}: {e}",
                                       reason="EncryptionUpdateFailed", bucket_name=bucket_name, error=str(e))
                        metrics.bucket_operations_total.labels(operation="update_encryption", result="failed").inc()
            elif current_algorithm is not None:
                self.log_info(meta, f"Drift detected: encryption is enabled on bucket {bucket_name} but desired state is disabled",
                             reason="DriftDetected", bucket_name=bucket_name, resource_type="encryption")
                metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="encryption").inc()

            # Check tags configuration
            desired_tags = bucket_config.get("tags") or {}
            if desired_tags:
                current_tags = provider_client.get_bucket_tags(bucket_name)
                if current_tags != desired_tags:
                    self.log_info(meta, f"Drift detected: tags configuration for bucket {bucket_name}",
                                 reason="DriftDetected", bucket_name=bucket_name, resource_type="tags")
                    metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="tags").inc()
                    provider_client.set_bucket_tags(bucket_name, desired_tags)
                    metrics.bucket_operations_total.labels(operation="update_tags", result="success").inc()

            # Check lifecycle configuration
            desired_lifecycle_rules = bucket_config.get("lifecycle_rules", [])
            if desired_lifecycle_rules:
                try:
                    current_lifecycle = provider_client.get_bucket_lifecycle(bucket_name)
                    lifecycle_changed = False

                    desired_lifecycle_normalized = json.dumps(
                        sorted(desired_lifecycle_rules, key=lambda x: x.get("id", ""))
                    )

                    if current_lifecycle is None:
                        lifecycle_changed = True
                    else:
                        current_rules = current_lifecycle.get("Rules", [])
                        current_crd_format = []
                        for rule in current_rules:
                            crd_rule: dict[str, Any] = {
                                "id": rule.get("ID"),
                                "status": rule.get("Status", "Enabled"),
                            }
                            if "Filter" in rule and "Prefix" in rule["Filter"]:
                                crd_rule["prefix"] = rule["Filter"]["Prefix"]
                            if "Expiration" in rule:
                                exp = rule["Expiration"]
                                if "Days" in exp:
                                    crd_rule["expiration"] = {"days": exp["Days"]}
                                elif "Date" in exp:
                                    crd_rule["expiration"] = {"date": exp["Date"]}
                            if "Transitions" in rule:
                                crd_rule["transitions"] = [
                                    {"days": t["Days"], "storageClass": t["StorageClass"]}
                                    for t in rule["Transitions"]
                                ]
                            current_crd_format.append(crd_rule)

                        current_lifecycle_normalized = json.dumps(
                            sorted(current_crd_format, key=lambda x: x.get("id", ""))
                        )
                        lifecycle_changed = desired_lifecycle_normalized != current_lifecycle_normalized

                    if lifecycle_changed:
                        self.log_info(meta, f"Drift detected: lifecycle configuration for bucket {bucket_name}",
                                     reason="DriftDetected", bucket_name=bucket_name, resource_type="lifecycle")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="lifecycle").inc()
                        provider_client.set_bucket_lifecycle(bucket_name, desired_lifecycle_rules)
                        metrics.bucket_operations_total.labels(operation="update_lifecycle", result="success").inc()
                except Exception as e:
                    self.log_warning(meta, f"Failed to reconcile lifecycle configuration for bucket {bucket_name}: {e}",
                                   reason="LifecycleReconcileFailed", bucket_name=bucket_name, error=str(e))
                    metrics.bucket_operations_total.labels(operation="update_lifecycle", result="failed").inc()
            elif bucket_config.get("lifecycle_rules") == []:
                try:
                    current_lifecycle = provider_client.get_bucket_lifecycle(bucket_name)
                    if current_lifecycle is not None:
                        self.log_info(meta, f"Drift detected: lifecycle should be removed for bucket {bucket_name}",
                                     reason="DriftDetected", bucket_name=bucket_name, resource_type="lifecycle")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="lifecycle").inc()
                        provider_client.delete_bucket_lifecycle(bucket_name)
                        metrics.bucket_operations_total.labels(operation="delete_lifecycle", result="success").inc()
                except Exception as e:
                    self.log_warning(meta, f"Failed to delete lifecycle configuration for bucket {bucket_name}: {e}",
                                   reason="LifecycleDeleteFailed", bucket_name=bucket_name, error=str(e))

            # Check CORS configuration
            desired_cors_rules = bucket_config.get("cors_rules", [])
            if desired_cors_rules:
                try:
                    current_cors = provider_client.get_bucket_cors(bucket_name)
                    cors_changed = False

                    desired_cors_normalized = json.dumps(
                        sorted(desired_cors_rules, key=lambda x: json.dumps(x.get("allowedOrigins", [])))
                    )

                    if current_cors is None:
                        cors_changed = True
                    else:
                        current_rules = current_cors.get("CORSRules", [])
                        current_crd_format = []
                        for rule in current_rules:
                            crd_rule: dict[str, Any] = {
                                "allowedOrigins": rule.get("AllowedOrigins", []),
                                "allowedMethods": rule.get("AllowedMethods", []),
                            }
                            if "AllowedHeaders" in rule:
                                crd_rule["allowedHeaders"] = rule["AllowedHeaders"]
                            if "ExposedHeaders" in rule:
                                crd_rule["exposedHeaders"] = rule["ExposedHeaders"]
                            if "MaxAgeSeconds" in rule:
                                crd_rule["maxAgeSeconds"] = rule["MaxAgeSeconds"]
                            current_crd_format.append(crd_rule)

                        current_cors_normalized = json.dumps(
                            sorted(current_crd_format, key=lambda x: json.dumps(x.get("allowedOrigins", [])))
                        )
                        cors_changed = desired_cors_normalized != current_cors_normalized

                    if cors_changed:
                        self.log_info(meta, f"Drift detected: CORS configuration for bucket {bucket_name}",
                                     reason="DriftDetected", bucket_name=bucket_name, resource_type="cors")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="cors").inc()
                        provider_client.set_bucket_cors(bucket_name, desired_cors_rules)
                        metrics.bucket_operations_total.labels(operation="update_cors", result="success").inc()
                except Exception as e:
                    self.log_warning(meta, f"Failed to reconcile CORS configuration for bucket {bucket_name}: {e}",
                                   reason="CORSReconcileFailed", bucket_name=bucket_name, error=str(e))
                    metrics.bucket_operations_total.labels(operation="update_cors", result="failed").inc()
            elif bucket_config.get("cors_rules") == []:
                try:
                    current_cors = provider_client.get_bucket_cors(bucket_name)
                    if current_cors is not None:
                        self.log_info(meta, f"Drift detected: CORS should be removed for bucket {bucket_name}",
                                     reason="DriftDetected", bucket_name=bucket_name, resource_type="cors")
                        metrics.drift_detected_total.labels(kind=KIND_BUCKET, resource_type="cors").inc()
                        provider_client.delete_bucket_cors(bucket_name)
                        metrics.bucket_operations_total.labels(operation="delete_cors", result="success").inc()
                except Exception as e:
                    self.log_warning(meta, f"Failed to delete CORS configuration for bucket {bucket_name}: {e}",
                                   reason="CORSDeleteFailed", bucket_name=bucket_name, error=str(e))

            emit_bucket_updated(meta, bucket_name)
            self.log_info(meta, f"Bucket {bucket_name} configuration reconciled",
                         reason="ConfigurationReconciled", bucket_name=bucket_name)
            metrics.bucket_operations_total.labels(operation="reconcile", result="success").inc()
        except Exception as e:
            self.log_warning(meta, f"Failed to reconcile bucket configuration for {bucket_name}: {e}",
                           reason="ReconciliationFailed", bucket_name=bucket_name, error=str(e))
            metrics.bucket_operations_total.labels(operation="reconcile", result="failed").inc()

    def _handle_auto_management(
        self,
        api: Any,
        namespace: str,
        name: str,
        bucket_name: str,
        provider_name: str,
        provider_ns: str,
        auto_manage: dict[str, Any],
        meta: dict[str, Any],
    ) -> str | None:
        """Handle bucket auto-management (user, access key, policy creation)."""
        try:
            user_name = auto_manage.get("userName", bucket_name)
            access_level = auto_manage.get("accessLevel", "readwrite")
            accesskey_crd_name = f"{name}-accesskey"
            user_crd_name = f"{name}-user"

            # Determine actions based on access level
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

            # Step 1: Create or get User
            user_ready = False
            try:
                existing_user = api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="users",
                    name=user_crd_name,
                )
                self.log_info(meta, f"User {user_crd_name} already exists",
                             reason="UserExists", user_crd_name=user_crd_name, bucket_name=bucket_name)
                user_status = existing_user.get("status", {})
                user_conditions = user_status.get("conditions", [])
                user_ready = any(
                    cond.get("type") == "Ready" and cond.get("status") == "True" for cond in user_conditions
                )
            except client.exceptions.ApiException as e:
                if e.status == 404:
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
                    self.log_info(meta, f"Creating User CRD {user_crd_name} with policy",
                                 reason="UserCreation", user_crd_name=user_crd_name, bucket_name=bucket_name)
                    api.create_namespaced_custom_object(
                        group="s3.cloud37.dev",
                        version="v1alpha1",
                        namespace=namespace,
                        plural="users",
                        body=user_body,
                    )
                    self.log_info(meta, f"Created user {user_crd_name} with inline policy",
                                 reason="UserCreated", user_crd_name=user_crd_name, bucket_name=bucket_name)

                    # Wait for user to be ready
                    max_wait_time = int(os.getenv("USER_READINESS_TIMEOUT_SECONDS", "60"))
                    wait_interval = 1
                    elapsed_time = 0

                    while not user_ready and elapsed_time < max_wait_time:
                        time.sleep(wait_interval)
                        elapsed_time += wait_interval

                        try:
                            user_obj = get_user_with_cache(api, user_crd_name, namespace)
                            user_status = user_obj.get("status", {})
                            user_conditions = user_status.get("conditions", [])
                            user_ready = any(
                                cond.get("type") == "Ready" and cond.get("status") == "True"
                                for cond in user_conditions
                            )

                            if user_ready:
                                self.log_info(meta, f"User {user_crd_name} is now ready",
                                             reason="UserReady", user_crd_name=user_crd_name, bucket_name=bucket_name)
                            # Note: debug logs remain as logger.debug since BaseHandler doesn't provide log_debug
                        except client.exceptions.ApiException:
                            pass

                    if not user_ready:
                        self.log_warning(meta, f"User {user_crd_name} not ready after {max_wait_time}s, proceeding anyway",
                                        reason="UserNotReadyTimeout", user_crd_name=user_crd_name, 
                                        max_wait_time=max_wait_time, bucket_name=bucket_name)
                else:
                    raise

            # Step 2: Create AccessKey (only if user is ready)
            if user_ready:
                try:
                    api.get_namespaced_custom_object(
                        group="s3.cloud37.dev",
                        version="v1alpha1",
                        namespace=namespace,
                        plural="accesskeys",
                        name=accesskey_crd_name,
                    )
                    self.log_info(meta, f"AccessKey {accesskey_crd_name} already exists",
                                 reason="AccessKeyExists", accesskey_crd_name=accesskey_crd_name, bucket_name=bucket_name)
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
                        self.log_info(meta, f"Created access key {accesskey_crd_name}",
                                     reason="AccessKeyCreated", accesskey_crd_name=accesskey_crd_name, bucket_name=bucket_name)
                    else:
                        raise
            else:
                self.log_warning(meta, f"Skipping AccessKey creation for {name} as user {user_crd_name} is not ready",
                               reason="AccessKeyCreationSkipped", name=name, user_crd_name=user_crd_name, bucket_name=bucket_name)

            # Step 3: Create BucketPolicy
            bucketpolicy_crd_name = f"{name}-policy"
            try:
                api.get_namespaced_custom_object(
                    group="s3.cloud37.dev",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="bucketpolicies",
                    name=bucketpolicy_crd_name,
                )
                self.log_info(meta, f"BucketPolicy {bucketpolicy_crd_name} already exists",
                             reason="BucketPolicyExists", bucketpolicy_crd_name=bucketpolicy_crd_name, bucket_name=bucket_name)
            except client.exceptions.ApiException as e:
                if e.status == 404:
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
                    self.log_info(meta, f"Creating BucketPolicy {bucketpolicy_crd_name} for user {user_name}",
                                 reason="BucketPolicyCreation", bucketpolicy_crd_name=bucketpolicy_crd_name, 
                                 user_name=user_name, bucket_name=bucket_name)
                    api.create_namespaced_custom_object(
                        group="s3.cloud37.dev",
                        version="v1alpha1",
                        namespace=namespace,
                        plural="bucketpolicies",
                        body=bucketpolicy_body,
                    )
                    self.log_info(meta, f"Created bucket policy {bucketpolicy_crd_name}",
                                 reason="BucketPolicyCreated", bucketpolicy_crd_name=bucketpolicy_crd_name, bucket_name=bucket_name)
                else:
                    raise

            return accesskey_crd_name
        except Exception as e:
            self.log_error(meta, f"Failed to auto-manage resources for bucket {bucket_name}", 
                          error=e, reason="AutoManagementFailed", bucket_name=bucket_name)
            return None

    def delete(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Handle Bucket resource deletion."""
        name = meta.get("name", "unknown")
        bucket_name = spec.get("name")

        self.log_info(meta, f"Bucket {name} is being deleted", event="deletion", reason="Deletion", bucket_name=bucket_name or name)

        if bucket_name:
            try:
                deletion_policy = spec.get("deletionPolicy", "Retain")
                force_delete = spec.get("forceDelete", False)

                self.log_info(meta, f"Deletion policy for bucket {bucket_name}: {deletion_policy}, forceDelete: {force_delete}",
                             reason="DeletionPolicy", bucket_name=bucket_name, deletion_policy=deletion_policy, force_delete=force_delete)

                provider_ref = spec.get("providerRef", {})
                provider_name = provider_ref.get("name")

                if provider_name:
                    api = get_k8s_client()
                    namespace = meta.get("namespace", "default")
                    provider_ns = provider_ref.get("namespace", namespace)

                    provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
                    provider_spec = provider_obj.get("spec", {})
                    provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                    if provider_client.bucket_exists(bucket_name):
                        if deletion_policy == "Delete":
                            provider_client.delete_bucket(bucket_name, force=force_delete)
                            emit_bucket_deleted(meta, bucket_name)
                            self.log_info(meta, f"Deleted bucket {bucket_name}", reason="BucketDeleted", bucket_name=bucket_name)
                        else:
                            self.log_info(meta, f"Retaining bucket {bucket_name} per deletionPolicy=Retain",
                                        reason="BucketRetained", bucket_name=bucket_name)
                            emit_bucket_deleted(meta, bucket_name)
                    else:
                        self.log_info(meta, f"Bucket {bucket_name} does not exist, skipping deletion",
                                     reason="BucketNotExists", bucket_name=bucket_name)
            except Exception as e:
                self.log_error(meta, f"Failed to delete bucket {bucket_name}", error=e, reason="DeletionFailed", bucket_name=bucket_name)
            finally:
                self.remove_finalizer(meta, patch)
        else:
            self.remove_finalizer(meta, patch)


# Global handler instance
_handler = BucketHandler()


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET)
@kopf.timer(API_GROUP_VERSION, KIND_BUCKET, interval=int(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "300")))
def handle_bucket(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle Bucket resource reconciliation."""
    _handler.ensure_finalizer(meta, patch)
    _handler.reconcile_with_metrics(meta, lambda: _handler.reconcile(spec, meta, status, patch))


@kopf.on.delete(API_GROUP_VERSION, KIND_BUCKET)
def handle_bucket_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle Bucket resource deletion."""
    _handler.delete(spec, meta, patch)
