"""Prometheus metrics for the S3 Operator."""

from prometheus_client import Counter, Histogram

# Reconciliation metrics
reconcile_total = Counter(
    "s3_operator_reconcile_total",
    "Total number of reconciliations",
    ["kind", "result"],
)

reconcile_duration_seconds = Histogram(
    "s3_operator_reconcile_duration_seconds",
    "Duration of reconciliations in seconds",
    ["kind"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# S3 operation metrics
bucket_operations_total = Counter(
    "s3_operator_bucket_operations_total",
    "Total number of S3 bucket operations",
    ["operation", "result"],
)

# Provider connectivity metrics
provider_connectivity = Counter(
    "s3_operator_provider_connectivity",
    "Provider connectivity status changes",
    ["provider", "status"],
)

