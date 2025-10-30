"""Tests for base handler functionality."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import kopf
import pytest

from wasabi_s3_operator.constants import FINALIZER
from wasabi_s3_operator.handlers.base import BaseHandler


class TestBaseHandler:
    """Test cases for BaseHandler class."""

    def test_init(self):
        """Test handler initialization."""
        handler = BaseHandler(kind="TestKind")
        assert handler.kind == "TestKind"
        assert handler.logger is not None

    def test_ensure_finalizer_adds_when_missing(self):
        """Test that finalizer is added when not present."""
        handler = BaseHandler(kind="TestKind")
        meta = {"finalizers": []}
        patch = kopf.Patch()

        handler.ensure_finalizer(meta, patch)

        assert "finalizers" in patch.metadata
        assert FINALIZER in patch.metadata["finalizers"]

    def test_ensure_finalizer_no_duplicate(self):
        """Test that finalizer is not duplicated if already present."""
        handler = BaseHandler(kind="TestKind")
        meta = {"finalizers": [FINALIZER, "other-finalizer"]}
        patch = kopf.Patch()

        handler.ensure_finalizer(meta, patch)

        # If finalizer already present, no patch is made
        # This is OK - the implementation appends to the existing list
        # The important thing is no error occurs
        if "finalizers" in patch.metadata:
            assert patch.metadata["finalizers"].count(FINALIZER) == 1

    def test_ensure_finalizer_creates_list_when_absent(self):
        """Test that finalizers list is created when absent."""
        handler = BaseHandler(kind="TestKind")
        meta = {}
        patch = kopf.Patch()

        handler.ensure_finalizer(meta, patch)

        assert "finalizers" in patch.metadata
        assert FINALIZER in patch.metadata["finalizers"]

    def test_remove_finalizer(self):
        """Test that finalizer is removed."""
        handler = BaseHandler(kind="TestKind")
        meta = {"finalizers": [FINALIZER, "other-finalizer"]}
        patch = kopf.Patch()

        handler.remove_finalizer(meta, patch)

        assert "finalizers" in patch.metadata
        assert FINALIZER not in patch.metadata["finalizers"]
        assert "other-finalizer" in patch.metadata["finalizers"]

    def test_remove_finalizer_sets_none_when_empty(self):
        """Test that finalizers is set to None when last finalizer is removed."""
        handler = BaseHandler(kind="TestKind")
        meta = {"finalizers": [FINALIZER]}
        patch = kopf.Patch()

        handler.remove_finalizer(meta, patch)

        assert "finalizers" in patch.metadata
        assert patch.metadata["finalizers"] is None

    def test_remove_finalizer_no_error_when_absent(self):
        """Test that removing absent finalizer doesn't error."""
        handler = BaseHandler(kind="TestKind")
        meta = {"finalizers": ["other-finalizer"]}
        patch = kopf.Patch()

        handler.remove_finalizer(meta, patch)

        # Should not raise an error - the method completes successfully
        # No patch may be made if finalizer wasn't present

    @patch("wasabi_s3_operator.handlers.base.emit_reconcile_started")
    @patch("wasabi_s3_operator.handlers.base.metrics")
    def test_reconcile_with_metrics_success(self, mock_metrics, mock_emit_started):
        """Test successful reconciliation with metrics."""
        handler = BaseHandler(kind="TestKind")
        meta = {"name": "test-resource", "namespace": "default"}
        reconcile_fn = Mock()

        handler.reconcile_with_metrics(meta, reconcile_fn)

        # Verify reconcile function was called
        reconcile_fn.assert_called_once()

        # Verify events
        mock_emit_started.assert_called_once_with(meta)

        # Verify metrics
        mock_metrics.reconcile_total.labels.assert_any_call(kind="TestKind", result="started")
        mock_metrics.reconcile_total.labels.assert_any_call(kind="TestKind", result="success")
        assert mock_metrics.reconcile_duration_seconds.labels.called

    @patch("wasabi_s3_operator.handlers.base.emit_reconcile_failed")
    @patch("wasabi_s3_operator.handlers.base.emit_reconcile_started")
    @patch("wasabi_s3_operator.handlers.base.metrics")
    @patch("wasabi_s3_operator.handlers.base.sanitize_exception")
    def test_reconcile_with_metrics_failure(
        self, mock_sanitize, mock_metrics, mock_emit_started, mock_emit_failed
    ):
        """Test failed reconciliation with metrics and error handling."""
        handler = BaseHandler(kind="TestKind")
        meta = {"name": "test-resource", "namespace": "default"}
        test_error = ValueError("Test error")
        mock_sanitize.return_value = "Sanitized error"

        def failing_fn():
            raise test_error

        with pytest.raises(ValueError):
            handler.reconcile_with_metrics(meta, failing_fn)

        # Verify error was sanitized (called twice: once for log_error, once for emit_reconcile_failed)
        # Both calls should be with the same error
        assert mock_sanitize.call_count == 2
        mock_sanitize.assert_any_call(test_error)

        # Verify events
        mock_emit_started.assert_called_once_with(meta)
        mock_emit_failed.assert_called_once_with(meta, "Reconciliation failed: Sanitized error")

        # Verify error metrics
        mock_metrics.error_total.labels.assert_called_with(
            kind="TestKind", error_type="ValueError"
        )
        mock_metrics.reconcile_total.labels.assert_any_call(kind="TestKind", result="error")

    @patch("wasabi_s3_operator.handlers.base.metrics")
    def test_update_resource_status_ready(self, mock_metrics):
        """Test updating resource status to ready."""
        handler = BaseHandler(kind="TestKind")
        meta = {"name": "test-resource", "generation": 5}
        patch = kopf.Patch()
        status_data = {"bucketName": "test-bucket", "ready": True}

        handler.update_resource_status(patch, meta, ready=True, status_data=status_data)

        assert patch.status["observedGeneration"] == 5
        assert patch.status["bucketName"] == "test-bucket"
        assert patch.status["ready"] is True

        # Verify metrics
        mock_metrics.resource_status_total.labels.assert_called_with(
            kind="TestKind", status="ready"
        )

    @patch("wasabi_s3_operator.handlers.base.metrics")
    def test_update_resource_status_not_ready(self, mock_metrics):
        """Test updating resource status to not ready."""
        handler = BaseHandler(kind="TestKind")
        meta = {"name": "test-resource", "generation": 3}
        patch = kopf.Patch()
        status_data = {"error": "Some error", "ready": False}

        handler.update_resource_status(patch, meta, ready=False, status_data=status_data)

        assert patch.status["observedGeneration"] == 3
        assert patch.status["error"] == "Some error"
        assert patch.status["ready"] is False

        # Verify metrics
        mock_metrics.resource_status_total.labels.assert_called_with(
            kind="TestKind", status="not_ready"
        )

    @patch("wasabi_s3_operator.handlers.base.metrics")
    def test_update_resource_status_minimal(self, mock_metrics):
        """Test updating resource status with minimal data."""
        handler = BaseHandler(kind="TestKind")
        meta = {"name": "test-resource"}
        patch = kopf.Patch()

        handler.update_resource_status(patch, meta, ready=True)

        assert patch.status["observedGeneration"] == 0
        # Verify metrics
        mock_metrics.resource_status_total.labels.assert_called_with(
            kind="TestKind", status="ready"
        )

