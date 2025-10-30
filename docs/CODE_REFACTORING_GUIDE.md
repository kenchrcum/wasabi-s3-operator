# Code Refactoring Guide

This document describes the standardized patterns introduced to reduce duplication, standardize logging, and add context propagation throughout the codebase.

## Overview

The refactoring introduces:
1. **Standardized Logging** - Structured logging with consistent context
2. **Common Error Handling Patterns** - Reusable error handling methods
3. **Context Propagation** - Correlation IDs and trace context propagation

## Standardized Logging

### Using Structured Logging Methods

Instead of using direct `logger.info()`, `logger.error()`, etc., use the standardized methods in `BaseHandler`:

```python
# ❌ Old way
self.logger.info(f"Bucket {bucket_name} created")
self.logger.error(f"Failed to create bucket: {error}")

# ✅ New way
self.log_info(meta, f"Bucket {bucket_name} created", reason="BucketCreated", bucket_name=bucket_name)
self.log_error(meta, f"Failed to create bucket: {error}", error=e, reason="CreationFailed")
```

### Available Logging Methods

All logging methods automatically include resource context (kind, name, namespace, uid):

- `log_info(meta, message, event="info", reason="Info", **kwargs)` - Info-level logs
- `log_warning(meta, message, event="warning", reason="Warning", **kwargs)` - Warning-level logs
- `log_error(meta, message, error=None, event="error", reason="Error", **kwargs)` - Error-level logs with optional exception

### Example

```python
def reconcile(self, spec, meta, status, patch):
    bucket_name = spec.get("name")
    
    # Info log with additional context
    self.log_info(meta, f"Reconciling bucket {bucket_name}", 
                  reason="Reconciliation", bucket_name=bucket_name)
    
    try:
        # ... do work ...
        self.log_info(meta, f"Bucket {bucket_name} created successfully",
                     reason="BucketCreated", bucket_name=bucket_name)
    except Exception as e:
        # Error log with exception details
        self.log_error(meta, f"Failed to create bucket {bucket_name}", 
                      error=e, reason="CreationFailed", bucket_name=bucket_name)
        raise
```

## Common Error Handling Patterns

### Validation Errors

Use `handle_validation_error()` for spec validation failures:

```python
# ❌ Old way
if not bucket_name:
    error_msg = "bucket name is required"
    emit_validate_failed(meta, error_msg)
    metrics.reconcile_total.labels(kind=KIND_BUCKET, result="failed").inc()
    raise ValueError(error_msg)

# ✅ New way
if not bucket_name:
    self.handle_validation_error(meta, "bucket name is required")
```

### Provider Not Found

Use `handle_provider_not_found()` when a provider resource is not found:

```python
# ❌ Old way
except client.exceptions.ApiException as e:
    if e.status == 404:
        error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
        self.logger.error(error_msg)
        conditions = status.get("conditions", [])
        conditions = set_provider_not_ready_condition(conditions, error_msg)
        emit_reconcile_failed(meta, error_msg)
        metrics.reconcile_total.labels(kind=self.kind, result="failed").inc()
        patch.status.update({
            "conditions": conditions,
            "observedGeneration": meta.get("generation", 0),
        })
        return

# ✅ New way
except client.exceptions.ApiException as e:
    if e.status == 404:
        error_msg = f"Provider {provider_name} not found in namespace {provider_ns}"
        self.handle_provider_not_found(meta, status, patch, provider_name, provider_ns, error_msg)
        return
```

### Provider Not Ready

Use `handle_provider_not_ready()` when a provider exists but is not ready:

```python
# ❌ Old way
if not provider_ready:
    error_msg = f"Provider {provider_name} is not ready"
    self.logger.warning(error_msg)
    conditions = status.get("conditions", [])
    conditions = set_provider_not_ready_condition(conditions, error_msg)
    emit_reconcile_failed(meta, error_msg)
    metrics.reconcile_total.labels(kind=self.kind, result="failed").inc()
    patch.status.update({
        "conditions": conditions,
        "observedGeneration": meta.get("generation", 0),
    })
    raise kopf.TemporaryError(error_msg)

# ✅ New way
if not provider_ready:
    error_msg = f"Provider {provider_name} is not ready"
    self.handle_provider_not_ready(meta, status, patch, provider_name, error_msg)
```

### General Reconciliation Errors

Use `handle_reconciliation_error()` for general reconciliation failures:

```python
# ❌ Old way
except Exception as e:
    sanitized_error = sanitize_exception(e)
    error_type = type(e).__name__
    self.logger.error(f"Reconciliation failed: {sanitized_error}")
    emit_reconcile_failed(meta, f"Reconciliation failed: {sanitized_error}")
    metrics.error_total.labels(kind=self.kind, error_type=error_type).inc()
    metrics.reconcile_total.labels(kind=self.kind, result="failed").inc()
    conditions = status.get("conditions", [])
    conditions = set_creation_failed_condition(conditions, sanitized_error)
    patch.status.update({
        "conditions": conditions,
        "observedGeneration": meta.get("generation", 0),
    })

# ✅ New way
except Exception as e:
    from ..utils.conditions import set_creation_failed_condition
    sanitized_error = sanitize_exception(e)
    self.handle_reconciliation_error(
        meta, status, patch, e,
        condition_fn=set_creation_failed_condition,
        condition_msg=sanitized_error
    )
```

## Status Updates

Use `update_resource_status()` for consistent status updates:

```python
# ❌ Old way
status_update = {
    "observedGeneration": meta.get("generation", 0),
    "conditions": conditions,
    "userId": user_id,
}
if ready:
    metrics.resource_status_total.labels(kind=self.kind, status="ready").inc()
else:
    metrics.resource_status_total.labels(kind=self.kind, status="not_ready").inc()
patch.status.update(status_update)

# ✅ New way
status_data = {
    "conditions": conditions,
    "userId": user_id,
}
self.update_resource_status(patch, meta, ready, status_data)
```

## Context Propagation

### Correlation IDs

Use correlation IDs to track operations across components:

```python
from ..utils.context import set_correlation_id, get_correlation_id, with_correlation_id

# Set correlation ID for current context
set_correlation_id("operation-123")

# Get correlation ID
corr_id = get_correlation_id()

# Use context manager
with with_correlation_id("operation-123"):
    # All operations in this block will have the correlation ID
    pass
```

### Trace Context

OpenTelemetry trace context is automatically propagated when using `trace_span()`:

```python
from ..tracing import trace_span

with trace_span("operation_name", kind=KIND_BUCKET):
    # Trace context is automatically available
    # All operations in this span will be traced
    pass
```

### Getting Context for Logs

Include context in log messages:

```python
from ..utils.context import get_context_dict

context = get_context_dict({"bucket_name": bucket_name})
self.log_info(meta, "Operation completed", **context)
```

## Migration Checklist

When refactoring a handler:

1. ✅ Replace `self.logger.info()` with `self.log_info(meta, ...)`
2. ✅ Replace `self.logger.warning()` with `self.log_warning(meta, ...)`
3. ✅ Replace `self.logger.error()` with `self.log_error(meta, ..., error=e)`
4. ✅ Replace validation error handling with `handle_validation_error()`
5. ✅ Replace provider not found handling with `handle_provider_not_found()`
6. ✅ Replace provider not ready handling with `handle_provider_not_ready()`
7. ✅ Replace status updates with `update_resource_status()`
8. ✅ Remove unused imports (`emit_validate_failed`, `emit_reconcile_failed`, etc.)
9. ✅ Add context propagation where appropriate

## Benefits

1. **Consistency**: All logs have the same structure with resource context
2. **Reduced Duplication**: Common error handling patterns are centralized
3. **Better Observability**: Context propagation enables better tracing
4. **Maintainability**: Changes to logging/error handling happen in one place
5. **Security**: Error sanitization is handled consistently

## Examples

See `handlers/provider.py` and `handlers/user.py` for examples of refactored handlers using these patterns.

