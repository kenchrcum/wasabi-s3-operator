# Grafana Dashboard for Wasabi S3 Operator

This directory contains Grafana dashboard configurations for monitoring the Wasabi S3 Operator.

## Dashboard Files

- `grafana-dashboard.json` - Main dashboard with comprehensive metrics visualization

## Installation

### Method 1: Import via Grafana UI

1. Open Grafana and navigate to Dashboards → Import
2. Click "Upload JSON file" and select `grafana-dashboard.json`
3. Select your Prometheus data source
4. Click "Import"

### Method 2: Provision via ConfigMap (Recommended for Kubernetes)

Create a ConfigMap with the dashboard:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: wasabi-s3-operator-dashboard
  namespace: monitoring
  labels:
    grafana_dashboard: "1"
data:
  wasabi-s3-operator.json: |
    # Paste contents of grafana-dashboard.json here
```

If using Grafana Operator, reference it in a GrafanaDashboard resource:

```yaml
apiVersion: integreatly.org/v1alpha1
kind: GrafanaDashboard
metadata:
  name: wasabi-s3-operator
  namespace: monitoring
spec:
  json: |
    # Paste contents of grafana-dashboard.json here
```

## Metrics Displayed

The dashboard visualizes the following Prometheus metrics:

### Reconciliation Metrics
- **Reconciliation Rate**: Rate of reconciliation operations by resource kind and result
- **Reconciliation Duration**: P50 and P95 latency percentiles for reconciliations
- **Resource Status**: Current status breakdown of all resources (ready/not_ready/error)

### Error Metrics
- **Error Rate**: Rate of errors by resource kind and error type
- **Error Types**: Breakdown of different error categories

### S3 Operations
- **Bucket Operations**: Rate of bucket create/update/delete operations
- **Operation Results**: Success vs failure rates

### Provider Metrics
- **Provider Connectivity**: Current connectivity status of providers
- **Connectivity History**: Historical connectivity patterns

### API Performance
- **API Call Rate**: Rate of Kubernetes and Wasabi API calls
- **API Call Duration**: Latency for API operations
- **Cache Hit Rate**: Effectiveness of Kubernetes API caching

### Configuration Management
- **Drift Detection**: Rate of configuration drift detections
- **Drift Types**: Types of configuration changes detected

### Rate Limiting
- **Rate Limit Hits**: Frequency of rate limit encounters by API type

## Customization

You can customize the dashboard by:

1. **Adding Variables**: Add template variables for filtering by namespace, resource name, etc.
2. **Adjusting Time Ranges**: Modify default time ranges in the dashboard JSON
3. **Adding Alerts**: Create alert rules based on dashboard panels
4. **Custom Panels**: Add additional panels for specific use cases

## Example Alerts

Consider creating alerts based on these thresholds:

```yaml
- alert: HighErrorRate
  expr: rate(wasabi_s3_operator_error_total[5m]) > 0.1
  for: 5m
  annotations:
    summary: "High error rate detected"

- alert: ProviderDisconnected
  expr: rate(wasabi_s3_operator_provider_connectivity_total{status="disconnected"}[5m]) > 0
  for: 1m
  annotations:
    summary: "Provider disconnected"

- alert: SlowReconciliation
  expr: histogram_quantile(0.95, rate(wasabi_s3_operator_reconcile_duration_seconds_bucket[5m])) > 10
  for: 5m
  annotations:
    summary: "Slow reconciliation detected"
```

## Troubleshooting

If metrics don't appear:

1. Verify Prometheus is scraping the operator's `/metrics` endpoint
2. Check that the ServiceMonitor or Prometheus scrape config is configured
3. Ensure the operator is exposing metrics on port 8080 (or configured port)
4. Verify metric names match exactly (case-sensitive)

## Reference

For more information about the metrics exposed by the operator, see:
- [Prometheus Metrics Documentation](../architecture/STATUS.md#metrics)
- [Operator Deployment Guide](../DEPLOYMENT.md)

