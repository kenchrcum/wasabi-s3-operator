# Code Organization and Observability Enhancements

This document describes the code organization improvements and observability enhancements completed for the Wasabi S3 Operator.

## Code Organization

### Handler Base Class

Created `src/wasabi_s3_operator/handlers/base.py` with `BaseHandler` class that provides:

- **Finalizer Management**: `ensure_finalizer()` and `remove_finalizer()` methods
- **Metrics Integration**: `reconcile_with_metrics()` wrapper for consistent metric collection
- **Status Updates**: `update_resource_status()` helper for standardized status updates
- **Error Handling**: Consistent error handling and logging patterns

### Handler Modules

All handlers have been fully migrated to modular structure under `src/wasabi_s3_operator/handlers/`:

- ✅ **`handlers/provider.py`** - Provider handler with tracing support
- ✅ **`handlers/bucket.py`** - Bucket handler
- ✅ **`handlers/bucket_policy.py`** - BucketPolicy handler
- ✅ **`handlers/access_key.py`** - AccessKey handler
- ✅ **`handlers/user.py`** - User handler
- ✅ **`handlers/iampolicy.py`** - IAMPolicy handler

### Shared Utilities

Created `src/wasabi_s3_operator/handlers/shared.py` with common utilities:

- `get_provider_with_cache()` - Cached provider lookup
- `get_user_with_cache()` - Cached user lookup
- `get_k8s_client()` - Kubernetes client factory

### Migration Status

All handlers have been fully migrated to use the BaseHandler structure:

```python
class ProviderHandler(BaseHandler):
    def reconcile(self, spec, meta, status, patch):
        # Uses tracing, base class methods, etc.
```

Each handler inherits from `BaseHandler` and follows the same patterns for finalizer management, metrics, and error handling.

## Observability Enhancements

### OpenTelemetry Tracing

Added comprehensive tracing support in `src/wasabi_s3_operator/tracing.py`:

#### Features

- **OTLP Exporter**: Supports exporting traces to OpenTelemetry Collector or compatible backends
- **Automatic Instrumentation**: Context managers for easy span creation
- **Graceful Degradation**: Works even if OpenTelemetry packages are not installed
- **Environment Configuration**: Configurable via environment variables

#### Configuration

Environment variables:

- `OTEL_TRACES_ENABLED` - Enable/disable tracing (default: `true`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` - OTLP endpoint URL (default: `http://localhost:4317`)
- `OTEL_SERVICE_NAME` - Service name (default: `wasabi-s3-operator`)
- `OTEL_SERVICE_VERSION` - Service version (optional)

#### Usage

```python
from ..tracing import trace_span

with trace_span("operation_name", kind="Provider", attributes={"key": "value"}):
    # Your code here
    # Spans are automatically created and errors are recorded
```

The Provider handler demonstrates tracing usage:

```python
with trace_span("reconcile_provider", kind=KIND_PROVIDER, attributes={"provider.name": name}):
    # Reconciliation logic
```

### Grafana Dashboard

Created comprehensive Grafana dashboard configuration:

#### Files

- **`docs/grafana-dashboard.json`** - Complete dashboard JSON configuration
- **`docs/grafana-dashboard-readme.md`** - Installation and usage guide

#### Dashboard Panels

The dashboard includes visualizations for:

1. **Reconciliation Metrics**
   - Reconciliation rate by kind and result
   - Reconciliation duration (P50, P95)
   - Resource status breakdown

2. **Error Tracking**
   - Error rate by kind and type
   - Error trends over time

3. **S3 Operations**
   - Bucket operation rates
   - Operation success/failure rates

4. **Provider Status**
   - Provider connectivity status
   - Connectivity history

5. **API Performance**
   - API call rates (K8s and Wasabi)
   - API call latency
   - Cache hit rates

6. **Configuration Management**
   - Configuration drift detection
   - Drift types and frequency

7. **Rate Limiting**
   - Rate limit hit frequency
   - Rate limit patterns

#### Installation

See `docs/grafana-dashboard-readme.md` for detailed installation instructions including:

- UI import method
- Kubernetes ConfigMap method
- Grafana Operator integration
- Alert rule examples

## Integration Points

### Main Entry Point

Updated `src/wasabi_s3_operator/main.py`:

- Imports tracing module
- Initializes tracing in `@kopf.on.startup()` hook
- All handlers imported from `handlers/` module (automatically registered via `handlers/__init__.py`)
- Main file reduced to 58 lines (down from 2,368 lines)

### Dependencies

Added to `requirements.txt`:

```
opentelemetry-api>=1.24.0,<2.0.0
opentelemetry-sdk>=1.24.0,<2.0.0
opentelemetry-exporter-otlp-proto-grpc>=1.24.0,<2.0.0
```

These are optional - the operator works without them, but tracing will be disabled.

## Future Enhancements

### Code Organization

1. ✅ **Complete Handler Migration**: All handlers migrated to use `BaseHandler`
2. ✅ **Shared Logic Extraction**: Common patterns extracted to shared utilities (`handlers/shared.py`)
3. **Handler Tests**: Create unit tests for handler classes

### Observability

1. **Additional Tracing**: Add traces to more operations (bucket creation, policy application, etc.)
2. **Distributed Tracing**: Add trace context propagation for multi-step operations
3. **Dashboard Enhancements**: Add template variables for filtering, add alert panels
4. **Trace Sampling**: Implement configurable trace sampling rates
5. **Custom Metrics**: Add business-level metrics (buckets created, keys rotated, etc.)

## References

- [STATUS.md](../architecture/STATUS.md) - Overall project status
- [Grafana Dashboard README](./grafana-dashboard-readme.md) - Dashboard installation guide
- [OpenTelemetry Python Documentation](https://opentelemetry.io/docs/instrumentation/python/)

