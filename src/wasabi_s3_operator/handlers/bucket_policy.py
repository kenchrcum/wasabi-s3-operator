"""Handler for BucketPolicy CRD."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import kopf
from kubernetes import client

from .. import metrics
from ..builders.provider import create_provider_from_spec
from ..constants import API_GROUP_VERSION, KIND_BUCKET_POLICY
from ..handlers.shared import get_provider_with_cache, get_k8s_client
from ..services.aws.client import AWSProvider
from ..tracing import trace_span
from ..utils.conditions import (
    set_apply_failed_condition,
    set_bucket_not_ready_condition,
    set_ready_condition,
)
from ..utils.events import (
    emit_policy_applied,
    emit_policy_failed,
    emit_reconcile_failed,
    emit_reconcile_started,
    emit_validate_failed,
    emit_validate_succeeded,
)
from .base import BaseHandler


class BucketPolicyHandler(BaseHandler):
    """Handler for BucketPolicy resources."""

    def __init__(self):
        """Initialize bucket policy handler."""
        super().__init__(KIND_BUCKET_POLICY)

    def reconcile(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        status: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Reconcile BucketPolicy resource."""
        namespace = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        bucket_ref = spec.get("bucketRef", {})
        policy = spec.get("policy", {})

        with trace_span("reconcile_bucket_policy", kind=KIND_BUCKET_POLICY, attributes={"policy.name": name}):
            # Validate spec
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

            if not isinstance(policy, dict) or "statement" not in policy:
                error_msg = "policy must contain 'statement' field"
                emit_validate_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                raise ValueError(error_msg)

            emit_validate_succeeded(meta)

            # Get bucket
            api = get_k8s_client()
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
                    self.logger.error(error_msg)
                    conditions = status.get("conditions", [])
                    conditions = set_bucket_not_ready_condition(conditions, error_msg)
                    emit_reconcile_failed(meta, error_msg)
                    metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                    patch.status.update({
                        "conditions": conditions,
                        "observedGeneration": meta.get("generation", 0),
                    })
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
                self.logger.warning(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_bucket_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                raise kopf.TemporaryError(error_msg)

            # Get bucket spec to find provider
            bucket_spec = bucket_obj.get("spec", {})
            provider_ref = bucket_spec.get("providerRef", {})
            provider_name = provider_ref.get("name")

            if not provider_name:
                error_msg = "Bucket provider reference not found"
                self.logger.error(error_msg)
                conditions = status.get("conditions", [])
                conditions = set_bucket_not_ready_condition(conditions, error_msg)
                emit_reconcile_failed(meta, error_msg)
                metrics.reconcile_total.labels(kind=KIND_BUCKET_POLICY, result="failed").inc()
                patch.status.update({
                    "conditions": conditions,
                    "observedGeneration": meta.get("generation", 0),
                })
                return

            # Get provider
            provider_ns = provider_ref.get("namespace", bucket_ns)
            provider_obj = get_provider_with_cache(api, provider_name, provider_ns, bucket_ns)

            # Create provider client
            provider_spec = provider_obj.get("spec", {})
            provider_client = create_provider_from_spec(provider_spec, provider_obj.get("metadata", {}))

            # Apply policy
            conditions = status.get("conditions", [])

            with trace_span("apply_bucket_policy", kind=KIND_BUCKET_POLICY):
                try:
                    # Check if bucket exists
                    if not provider_client.bucket_exists(bucket_name):
                        error_msg = f"Bucket {bucket_name} does not exist in provider"
                        self.logger.error(error_msg)
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
                            if isinstance(provider_client, AWSProvider):
                                current_policy_normalized = json.dumps(current_policy, sort_keys=True)
                                desired_policy_normalized = json.dumps(
                                    provider_client._convert_policy_to_aws_format(policy),
                                    sort_keys=True
                                )
                                policy_changed = current_policy_normalized != desired_policy_normalized

                                if not policy_changed:
                                    self.logger.info(f"Policy for bucket {bucket_name} unchanged, skipping update")
                                else:
                                    self.logger.info(f"Drift detected: policy for bucket {bucket_name}")
                                    metrics.drift_detected_total.labels(kind=KIND_BUCKET_POLICY, resource_type="policy").inc()
                        else:
                            self.logger.info(f"No existing policy for bucket {bucket_name}, will create new policy")
                            policy_changed = True
                    except Exception as e:
                        self.logger.debug(f"Could not get current policy for comparison: {e}")
                        policy_changed = True

                    # Apply policy only if it changed
                    if policy_changed:
                        provider_client.set_bucket_policy(bucket_name, policy)
                        emit_policy_applied(meta, bucket_name)
                        self.logger.info(f"Applied policy to bucket {bucket_name}")
                    else:
                        self.logger.info(f"Policy for bucket {bucket_name} is already up to date")

                    # Set ready condition
                    conditions = set_ready_condition(conditions, True, f"Policy applied to bucket {bucket_name}")

                except Exception as e:
                    error_msg = f"Failed to apply policy: {str(e)}"
                    self.logger.error(error_msg)
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

    def delete(
        self,
        spec: dict[str, Any],
        meta: dict[str, Any],
        patch: kopf.Patch,
    ) -> None:
        """Handle BucketPolicy resource deletion."""
        name = meta.get("name", "unknown")
        bucket_ref = spec.get("bucketRef", {})
        bucket_name = bucket_ref.get("name")

        self.logger.info(f"BucketPolicy {name} is being deleted")

        if bucket_name:
            try:
                api = get_k8s_client()
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

                    if provider_client.bucket_exists(bucket_name):
                        provider_client.delete_bucket_policy(bucket_name)
                        self.logger.info(f"Deleted policy from bucket {bucket_name}")
            except Exception as e:
                self.logger.error(f"Failed to delete policy from bucket {bucket_name}: {e}")
            finally:
                self.remove_finalizer(meta, patch)
        else:
            self.remove_finalizer(meta, patch)


# Global handler instance
_handler = BucketPolicyHandler()


@kopf.on.create(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.update(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.on.resume(API_GROUP_VERSION, KIND_BUCKET_POLICY)
@kopf.timer(API_GROUP_VERSION, KIND_BUCKET_POLICY, interval=int(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "300")))
def handle_bucket_policy(
    spec: dict[str, Any],
    meta: dict[str, Any],
    status: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle BucketPolicy resource reconciliation."""
    _handler.ensure_finalizer(meta, patch)
    _handler.reconcile_with_metrics(meta, lambda: _handler.reconcile(spec, meta, status, patch))


@kopf.on.delete(API_GROUP_VERSION, KIND_BUCKET_POLICY)
def handle_bucket_policy_delete(
    spec: dict[str, Any],
    meta: dict[str, Any],
    patch: kopf.Patch,
    **kwargs: Any,
) -> None:
    """Handle BucketPolicy resource deletion."""
    _handler.delete(spec, meta, patch)
