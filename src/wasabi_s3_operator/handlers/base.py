"""Base handler class with common functionality for all CRD handlers."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import kopf

from .. import metrics
from ..constants import FINALIZER
from ..utils.errors import sanitize_exception
from ..utils.events import emit_reconcile_failed, emit_reconcile_started


class BaseHandler:
    """Base class for all CRD handlers with common functionality."""

    def __init__(self, kind: str):
        """Initialize base handler.
        
        Args:
            kind: The Kubernetes resource kind (e.g., "Provider", "Bucket")
        """
        self.kind = kind
        self.logger = logging.getLogger(__name__)

    def ensure_finalizer(self, meta: dict[str, Any], patch: kopf.Patch) -> None:
        """Ensure finalizer is present in metadata."""
        finalizers = meta.get("finalizers", [])
        if FINALIZER not in finalizers:
            finalizers.append(FINALIZER)
            patch.metadata["finalizers"] = finalizers

    def remove_finalizer(self, meta: dict[str, Any], patch: kopf.Patch) -> None:
        """Remove finalizer from metadata."""
        finalizers = meta.get("finalizers", [])
        if FINALIZER in finalizers:
            finalizers.remove(FINALIZER)
            patch.metadata["finalizers"] = finalizers if finalizers else None

    def reconcile_with_metrics(
        self,
        meta: dict[str, Any],
        reconcile_fn: Callable[[], None],
    ) -> None:
        """Execute reconciliation with metrics and error handling.
        
        Args:
            meta: Kubernetes resource metadata
            reconcile_fn: Function to execute for reconciliation
        """
        name = meta.get("name", "unknown")
        
        emit_reconcile_started(meta)
        metrics.reconcile_total.labels(kind=self.kind, result="started").inc()
        
        start_time = time.time()
        try:
            reconcile_fn()
            metrics.reconcile_total.labels(kind=self.kind, result="success").inc()
        except Exception as e:
            sanitized_error = sanitize_exception(e)
            error_type = type(e).__name__
            metrics.error_total.labels(kind=self.kind, error_type=error_type).inc()
            self.logger.error(f"{self.kind} reconciliation failed for {name}: {sanitized_error}")
            emit_reconcile_failed(meta, f"Reconciliation failed: {sanitized_error}")
            metrics.reconcile_total.labels(kind=self.kind, result="error").inc()
            raise
        finally:
            duration = time.time() - start_time
            metrics.reconcile_duration_seconds.labels(kind=self.kind).observe(duration)

    def update_resource_status(
        self,
        patch: kopf.Patch,
        meta: dict[str, Any],
        ready: bool,
        status_data: dict[str, Any] | None = None,
    ) -> None:
        """Update resource status with common fields.
        
        Args:
            patch: Kopf patch object
            meta: Kubernetes resource metadata
            ready: Whether the resource is ready
            status_data: Additional status data to include
        """
        status_update = {
            "observedGeneration": meta.get("generation", 0),
            **(status_data or {}),
        }
        
        if ready:
            metrics.resource_status_total.labels(kind=self.kind, status="ready").inc()
        else:
            metrics.resource_status_total.labels(kind=self.kind, status="not_ready").inc()
        
        patch.status.update(status_update)

