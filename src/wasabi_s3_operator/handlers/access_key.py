"""Handler for AccessKey CRD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import kopf
from kubernetes import client

from .. import metrics
from ..builders.provider import create_provider_from_spec
from ..constants import API_GROUP_VERSION, KIND_ACCESS_KEY
from ..handlers.shared import get_provider_with_cache, get_user_with_cache, get_k8s_client
from ..tracing import trace_span
from ..utils.access_keys import create_access_key_secret, update_access_key_secret
from ..utils.conditions import (
    set_creation_failed_condition,
    set_provider_not_ready_condition,
    set_ready_condition,
    set_rotation_failed_condition,
)
from ..utils.errors import sanitize_exception
from ..utils.events import (
    emit_access_key_created,
    emit_access_key_rotated,
    emit_reconcile_failed,
    emit_reconcile_started,
    emit_validate_failed,
    emit_validate_succeeded,
)
from ..utils.secrets import (
    cleanup_expired_previous_secrets,
    create_previous_secret,
    list_previous_secrets,
    read_secret_data,
)
from .base import BaseHandler


class AccessKeyHandler(BaseHandler):
    """Handler for AccessKey resources."""

    def __init__(self):
        """Initialize access key handler."""
        super().__init__(KIND_ACCESS_KEY)

    def reconcile(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Reconcile AccessKey resource."""
        namespace = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")

        with trace_span("reconcile_access_key", kind=KIND_ACCESS_KEY, attributes={"accesskey.name": name}):
            # Validate spec
            if not provider_name:
                error_msg = "providerRef.name is required"
                emit_validate_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                raise ValueError(error_msg)

            emit_validate_succeeded(meta)

            # Get provider
            api = get_k8s_client()
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
                    self.logger.error(error_msg)
                    conditions = status.get("conditions", [])
                    conditions = set_provider_not_ready_condition(conditions, error_msg)
                    emit_reconcile_failed(meta, error_msg)
                    metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
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
                self.logger.warning(error_msg)
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

            # Validate userRef
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
                    self.logger.error(error_msg)
                    conditions = status.get("conditions", [])
                    conditions = set_provider_not_ready_condition(conditions, error_msg)
                    emit_reconcile_failed(meta, error_msg)
                    metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                    patch.status.update({
                        "conditions": conditions,
                        "observedGeneration": meta.get("generation", 0),
                    })
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
                self.logger.warning(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_provider_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                raise kopf.TemporaryError(error_msg)

            # Get IAM user name
            user_spec = user_obj.get("spec", {})
            iam_user_name = user_spec.get("name")
            if not iam_user_name:
                error_msg = f"User {user_name} does not have a valid IAM user name in spec"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            # Check existing key and rotation
            existing_key_id = status.get("accessKeyId")
            conditions = status.get("conditions", [])
            rotate_config = spec.get("rotate", {})
            rotation_enabled = rotate_config.get("enabled", False)
            rotation_interval_days = rotate_config.get("intervalDays", 90)
            retention_days = rotate_config.get("previousKeysRetentionDays", 7)

            needs_rotation = False
            if rotation_enabled and existing_key_id:
                next_rotate_time_str = status.get("nextRotateTime")
                if next_rotate_time_str:
                    try:
                        next_rotate_time = datetime.fromisoformat(next_rotate_time_str.replace('Z', '+00:00'))
                        if datetime.now(timezone.utc) >= next_rotate_time:
                            needs_rotation = True
                            self.logger.info(f"Access key {existing_key_id} needs rotation")
                    except Exception as e:
                        self.logger.warning(f"Failed to parse nextRotateTime: {e}")

            if not existing_key_id:
                self._create_access_key(
                    provider_client, iam_user_name, name, namespace, meta, status, patch,
                    rotation_enabled, rotation_interval_days, conditions
                )
            elif needs_rotation:
                self._rotate_access_key(
                    provider_client, iam_user_name, existing_key_id, name, namespace, meta, status, patch,
                    rotation_interval_days, conditions
                )
            else:
                self._maintain_access_key(
                    provider_client, iam_user_name, existing_key_id, name, namespace, status, patch,
                    rotation_enabled, retention_days, conditions
                )

    def _create_access_key(
        self,
        provider_client: Any,
        iam_user_name: str,
        name: str,
        namespace: str,
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
        rotation_enabled: bool,
        rotation_interval_days: int,
        conditions: list[dict[str, Any]],
    ) -> None:
        """Create a new access key."""
        with trace_span("create_access_key", kind=KIND_ACCESS_KEY):
            try:
                key_response = provider_client.create_access_key(iam_user_name)
                access_key_id = key_response.get("AccessKey", {}).get("AccessKeyId")
                secret_access_key = key_response.get("AccessKey", {}).get("SecretAccessKey")

                # Create Kubernetes secret
                secret_name = f"{name}-credentials"
                core_api = client.CoreV1Api()
                try:
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
                    self.logger.info(f"Created access key {access_key_id} for user {iam_user_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 409:
                        self.logger.info(f"Secret {secret_name} already exists")
                    else:
                        raise

                # Calculate next rotation time
                last_rotate_time = datetime.now(timezone.utc).isoformat()
                next_rotate_time = None
                if rotation_enabled:
                    next_rotate_time = (datetime.now(timezone.utc) + timedelta(days=rotation_interval_days)).isoformat()

                conditions = set_ready_condition(conditions, True, f"Access key {access_key_id} created for user {iam_user_name}")

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
                self.logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })

    def _rotate_access_key(
        self,
        provider_client: Any,
        iam_user_name: str,
        existing_key_id: str,
        name: str,
        namespace: str,
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
        rotation_interval_days: int,
        conditions: list[dict[str, Any]],
    ) -> None:
        """Rotate an existing access key."""
        with trace_span("rotate_access_key", kind=KIND_ACCESS_KEY):
            try:
                self.logger.info(f"Rotating access key {existing_key_id} for user {iam_user_name}")

                # Read current secret
                secret_name = f"{name}-credentials"
                core_api = client.CoreV1Api()
                old_secret_data = read_secret_data(core_api, namespace, secret_name)
                old_access_key_id = old_secret_data.get("access-key-id")
                old_secret_access_key = old_secret_data.get("secret-access-key")

                if not old_access_key_id or not old_secret_access_key:
                    raise ValueError("Current secret missing access key credentials")

                # Create new access key
                key_response = provider_client.create_access_key(iam_user_name)
                new_access_key_id = key_response.get("AccessKey", {}).get("AccessKeyId")
                new_secret_access_key = key_response.get("AccessKey", {}).get("SecretAccessKey")

                self.logger.info(f"Created new access key {new_access_key_id}")

                # Create previous secret
                rotated_at = datetime.now(timezone.utc).isoformat()
                timestamp_str = rotated_at.replace("-", "").replace(":", "").replace(".", "").split("+")[0].split("T")
                timestamp_str = "".join(timestamp_str)[:14]
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
                self.logger.info(f"Created previous secret {previous_secret_name}")

                # Update main secret
                update_access_key_secret(
                    core_api,
                    namespace,
                    secret_name,
                    new_access_key_id,
                    new_secret_access_key,
                )
                self.logger.info(f"Updated secret {secret_name} with new credentials")

                # Calculate next rotation time
                last_rotate_time = rotated_at
                next_rotate_time = (datetime.now(timezone.utc) + timedelta(days=rotation_interval_days)).isoformat()

                emit_access_key_rotated(meta, new_access_key_id)
                conditions = set_ready_condition(conditions, True, f"Access key rotated to {new_access_key_id}")

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
                self.logger.error(error_msg)
                conditions = set_rotation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })

    def _maintain_access_key(
        self,
        provider_client: Any,
        iam_user_name: str,
        existing_key_id: str,
        name: str,
        namespace: str,
        status: dict[str, Any],
        patch: kopf.Patch,
        rotation_enabled: bool,
        retention_days: int,
        conditions: list[dict[str, Any]],
    ) -> None:
        """Maintain existing access key (cleanup expired secrets)."""
        self.logger.info(f"Access key {existing_key_id} already exists")

        if rotation_enabled:
            core_api = client.CoreV1Api()
            try:
                expired_secrets = list_previous_secrets(
                    core_api,
                    namespace,
                    name,
                    include_expired=True,
                    retention_days=retention_days,
                )
                expired_secrets = [s for s in expired_secrets if s.get("is_expired", False)]

                # Delete expired access keys from Wasabi
                for secret_info in expired_secrets:
                    try:
                        secret_data = read_secret_data(core_api, namespace, secret_info["name"])
                        expired_key_id = secret_data.get("access-key-id")

                        if expired_key_id:
                            try:
                                provider_client.delete_access_key(iam_user_name, expired_key_id)
                                self.logger.info(f"Deleted expired access key {expired_key_id} from Wasabi")
                            except Exception as e:
                                self.logger.warning(f"Failed to delete expired access key {expired_key_id} from Wasabi: {e}")
                    except Exception as e:
                        self.logger.warning(f"Failed to read secret {secret_info['name']} for cleanup: {e}")

                # Cleanup expired secrets
                deleted_secrets = cleanup_expired_previous_secrets(
                    core_api,
                    namespace,
                    name,
                    retention_days,
                )

                if deleted_secrets:
                    self.logger.info(f"Deleted {len(deleted_secrets)} expired previous secrets: {deleted_secrets}")
            except Exception as e:
                self.logger.warning(f"Failed to cleanup expired previous secrets: {e}")

        conditions = set_ready_condition(conditions, True, f"Access key {existing_key_id} is ready")

        status_update = {
            "observedGeneration": meta.get("generation", 0),
            "accessKeyId": existing_key_id,
            "created": True,
            "conditions": conditions,
        }

        if rotation_enabled:
            if status.get("lastRotateTime"):
                status_update["lastRotateTime"] = status.get("lastRotateTime")
            if status.get("nextRotateTime"):
                status_update["nextRotateTime"] = status.get("nextRotateTime")

        metrics.reconcile_total.labels(kind=KIND_ACCESS_KEY, result="success").inc()
        patch.status.update(status_update)

    def delete(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Handle AccessKey resource deletion."""
        name = meta.get("name", "unknown")
        namespace = meta.get("namespace", "default")
        access_key_id = status.get("accessKeyId")

        self.logger.info(f"AccessKey {name} is being deleted")

        if access_key_id:
            try:
                provider_ref = spec.get("providerRef", {})
                provider_name = provider_ref.get("name")

                if provider_name:
                    api = get_k8s_client()
                    provider_ns = provider_ref.get("namespace", namespace)

                    try:
                        provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
                        provider_spec = provider_obj.get("spec", {})
                        provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                        user_ref = spec.get("userRef", {})
                        user_name = user_ref.get("name")

                        if user_name:
                            user_ns = user_ref.get("namespace", namespace)
                            try:
                                user_obj = get_user_with_cache(api, user_name, user_ns)
                                user_spec = user_obj.get("spec", {})
                                iam_user_name = user_spec.get("name")

                                if iam_user_name:
                                    provider_client.delete_access_key(iam_user_name, access_key_id)
                                    self.logger.info(f"Deleted access key {access_key_id} for user {iam_user_name}")
                                else:
                                    self.logger.warning(f"User {user_name} does not have IAM user name in spec")
                            except client.exceptions.ApiException as e:
                                if e.status == 404:
                                    self.logger.warning(f"User {user_name} not found, cannot delete access key")
                                else:
                                    raise
                        else:
                            self.logger.warning(f"AccessKey {name} does not have userRef, cannot delete access key")
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            self.logger.warning(f"Provider {provider_name} not found, cannot delete access key")
                        else:
                            raise
            except Exception as e:
                self.logger.error(f"Failed to delete access key {access_key_id}: {e}")

        self.remove_finalizer(meta, patch)


# Global handler instance
_handler = AccessKeyHandler()


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
    _handler.ensure_finalizer(meta, patch)
    _handler.reconcile_with_metrics(meta, lambda: _handler.reconcile(spec, meta, status, patch))


@kopf.on.delete(API_GROUP_VERSION, KIND_ACCESS_KEY)
def handle_access_key_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle AccessKey resource deletion."""
    _handler.delete(spec, meta, status, patch)
