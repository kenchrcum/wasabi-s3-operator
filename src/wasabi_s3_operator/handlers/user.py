"""Handler for User CRD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import kopf
from kubernetes import client

from .. import metrics
from ..builders.provider import create_provider_from_spec
from ..constants import API_GROUP_VERSION, KIND_USER
from ..handlers.shared import get_provider_with_cache, get_k8s_client
from ..tracing import trace_span
from ..utils.conditions import (
    set_creation_failed_condition,
    set_provider_not_ready_condition,
    set_ready_condition,
)
from ..utils.events import emit_validate_succeeded
from .base import BaseHandler


class UserHandler(BaseHandler):
    """Handler for User resources."""

    def __init__(self):
        """Initialize user handler."""
        super().__init__(KIND_USER)

    def reconcile(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Reconcile User resource."""
        namespace = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")
        user_name = spec.get("name")

        with trace_span("reconcile_user", kind=KIND_USER, attributes={"user.name": user_name or name}):
            # Validate spec
            if not provider_name:
                self.handle_validation_error(meta, "providerRef.name is required")

            if not user_name:
                self.handle_validation_error(meta, "user name is required")

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

            # Check if user already exists
            existing_user_id = status.get("userId")
            conditions = status.get("conditions", [])

            if not existing_user_id:
                self._create_user(provider_client, api, namespace, user_name, spec, meta, status, patch, conditions)
            else:
                # User already exists
                self.log_info(meta, f"User {user_name} already exists", reason="UserExists", user_name=user_name)
                conditions = set_ready_condition(conditions, True, f"User {user_name} is ready")

                status_update = {
                    "observedGeneration": meta.get("generation", 0),
                    "userId": existing_user_id,
                    "created": True,
                    "conditions": conditions,
                }

                metrics.reconcile_total.labels(kind=KIND_USER, result="success").inc()
                patch.status.update(status_update)

    def _create_user(
        self,
        provider_client: Any,
        api: Any,
        namespace: str,
        user_name: str,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
        conditions: list[dict[str, Any]],
    ) -> None:
        """Create a new user."""
        with trace_span("create_user", kind=KIND_USER):
            try:
                policy = spec.get("policy")
                policy_ref = spec.get("policyRef")

                # Check for mutually exclusive policy options
                if policy and policy_ref:
                    error_msg = "Cannot specify both policy and policyRef"
                    self.logger.error(error_msg)
                    conditions = set_creation_failed_condition(conditions, error_msg)
                    emit_reconcile_failed(meta, error_msg)
                    metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                    patch.status.update({
                        "conditions": conditions,
                        "observedGeneration": meta.get("generation", 0),
                    })
                    return

                # If policyRef is provided, fetch the IAMPolicy
                policy_name = None
                if policy_ref:
                    policy_name = policy_ref.get("name")
                    policy_ns = policy_ref.get("namespace", namespace)

                    if not policy_name:
                        error_msg = "policyRef.name is required"
                        self.logger.error(error_msg)
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
                            self.logger.warning(error_msg)
                            conditions = set_creation_failed_condition(conditions, error_msg)
                            emit_reconcile_failed(meta, error_msg)
                            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                            patch.status.update({
                                "conditions": conditions,
                                "observedGeneration": meta.get("generation", 0),
                            })
                            raise kopf.TemporaryError(error_msg)

                        self.logger.info(f"Will attach managed policy {policy_name} to user {user_name}")
                        policy = None  # Set to None to indicate we're using policyRef
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            error_msg = f"IAMPolicy {policy_name} not found in namespace {policy_ns}"
                            self.logger.error(error_msg)
                            conditions = set_creation_failed_condition(conditions, error_msg)
                            emit_reconcile_failed(meta, error_msg)
                            metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                            patch.status.update({
                                "conditions": conditions,
                                "observedGeneration": meta.get("generation", 0),
                            })
                            return
                        raise

                # If no policy provided, create a default policy
                if not policy:
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
                    self.logger.info(f"No policy provided, creating default policy for bucket {bucket_name}")

                # Create user (with or without inline policy)
                if policy:
                    self.logger.info(f"Creating user {user_name} with inline policy: {policy}")
                    user_response = provider_client.create_user(user_name, policy)
                else:
                    self.logger.info(f"Creating user {user_name} without inline policy")
                    user_response = provider_client.create_user(user_name, None)

                user_id = user_response.get("User", {}).get("UserId")

                # If policyRef was specified, attach the managed policy
                if policy_ref and policy_name:
                    try:
                        self.logger.info(f"Attaching managed policy {policy_name} to user {user_name}")
                        provider_client.attach_managed_policy_to_user(user_name, policy_name)
                        self.logger.info(f"Successfully attached managed policy {policy_name} to user {user_name}")
                    except Exception as e:
                        error_msg = f"Failed to attach managed policy {policy_name}: {str(e)}"
                        self.logger.error(error_msg)
                        # Don't fail user creation if policy attachment fails

                conditions = set_ready_condition(conditions, True, f"User {user_name} created")
                self.logger.info(f"Created user {user_name} with ID {user_id}")

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
                self.logger.error(error_msg)
                conditions = set_creation_failed_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_USER, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })

    def delete(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Handle User resource deletion."""
        name = meta.get("name", "unknown")
        user_name = spec.get("name")

        self.log_info(meta, f"User {name} is being deleted", event="deletion", reason="Deletion", user_name=name)

        if user_name:
            try:
                provider_ref = spec.get("providerRef", {})
                provider_name = provider_ref.get("name")

                if provider_name:
                    api = get_k8s_client()
                    namespace = meta.get("namespace", "default")
                    provider_ns = provider_ref.get("namespace", namespace)

                    provider_obj = get_provider_with_cache(api, provider_name, provider_ns, namespace)
                    provider_spec = provider_obj.get("spec", {})
                    provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                    provider_client.delete_user(user_name)
                    self.logger.info(f"Deleted user {user_name}")
            except Exception as e:
                self.logger.error(f"Failed to delete user {user_name}: {e}")
            finally:
                self.remove_finalizer(meta, patch)
        else:
            self.remove_finalizer(meta, patch)


# Global handler instance
_handler = UserHandler()


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
    _handler.ensure_finalizer(meta, patch)
    _handler.reconcile_with_metrics(meta, lambda: _handler.reconcile(spec, meta, status, patch))


@kopf.on.delete(API_GROUP_VERSION, KIND_USER)
def handle_user_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle User resource deletion."""
    _handler.delete(spec, meta, patch)
