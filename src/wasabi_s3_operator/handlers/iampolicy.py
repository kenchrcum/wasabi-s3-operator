"""Handler for IAMPolicy CRD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import kopf
from kubernetes import client

from .. import metrics
from ..builders.provider import create_provider_from_spec
from ..constants import API_GROUP_VERSION, KIND_IAM_POLICY
from ..handlers.shared import get_provider_with_cache, get_k8s_client
from ..services.aws.client import AWSProvider
from ..tracing import trace_span
from ..utils.conditions import (
    set_attach_failed_condition,
    set_provider_not_ready_condition,
    set_ready_condition,
)
from ..utils.events import (
    emit_reconcile_failed,
    emit_reconcile_started,
    emit_validate_failed,
    emit_validate_succeeded,
)
from .base import BaseHandler


class IAMPolicyHandler(BaseHandler):
    """Handler for IAMPolicy resources."""

    def __init__(self):
        """Initialize IAM policy handler."""
        super().__init__(KIND_IAM_POLICY)

    def reconcile(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Reconcile IAMPolicy resource."""
        namespace = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        provider_ref = spec.get("providerRef", {})
        provider_name = provider_ref.get("name")
        policy = spec.get("policy", {})

        with trace_span("reconcile_iampolicy", kind=KIND_IAM_POLICY, attributes={"policy.name": name}):
            # Validate spec
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

            if not isinstance(policy, dict) or "statement" not in policy:
                error_msg = "policy must contain 'statement' field"
                emit_validate_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_IAM_POLICY, result="failed").inc()
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
                self.logger.warning(error_msg)
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
            if isinstance(provider_client, AWSProvider):
                aws_policy = provider_client._convert_policy_to_aws_format(policy)
            else:
                aws_policy = policy

            # Create managed policy
            conditions = status.get("conditions", [])
            policy_arn = None

            with trace_span("create_managed_policy", kind=KIND_IAM_POLICY):
                try:
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

                    self.logger.info(f"Created managed policy {name} with ARN {policy_arn}")
                    conditions = set_ready_condition(conditions, True, f"IAMPolicy {name} is ready")

                except Exception as e:
                    error_msg = f"Failed to create managed policy: {str(e)}"
                    self.logger.error(error_msg)
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

    def delete(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Handle IAMPolicy resource deletion."""
        name = meta.get("name", "unknown")
        namespace = meta.get("namespace", "default")

        self.logger.info(f"IAMPolicy {name} is being deleted")

        try:
            provider_ref = spec.get("providerRef", {})
            provider_name = provider_ref.get("name")

            if provider_name:
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

                    provider_spec = provider_obj.get("spec", {})
                    provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

                    provider_client.delete_managed_policy(name)
                    self.logger.info(f"Deleted managed policy {name} from Wasabi")
                except Exception as e:
                    self.logger.error(f"Failed to delete managed policy {name}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to delete IAMPolicy {name}: {e}")
        finally:
            self.remove_finalizer(meta, patch)


# Global handler instance
_handler = IAMPolicyHandler()


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
    _handler.ensure_finalizer(meta, patch)
    _handler.reconcile_with_metrics(meta, lambda: _handler.reconcile(spec, meta, status, patch))


@kopf.on.delete(API_GROUP_VERSION, KIND_IAM_POLICY)
def handle_iampolicy_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle IAMPolicy resource deletion."""
    _handler.delete(spec, meta, patch)
