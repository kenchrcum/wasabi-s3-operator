"""Prometheus metrics for the Wasabi S3 Provider Operator."""

from prometheus_client import Counter, Histogram

# Reconciliation metrics
reconcile_total = Counter(
    "wasabi_s3_provider_reconcile_total",
    "Total number of reconciliations",
    ["kind", "result"],
)

reconcile_duration_seconds = Histogram(
    "wasabi_s3_provider_reconcile_duration_seconds",
    "Duration of reconciliations in seconds",
    ["kind"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# S3 operation metrics
bucket_operations_total = Counter(
    "wasabi_s3_provider_bucket_operations_total",
    "Total number of S3 bucket operations",
    ["operation", "result"],
)

# Provider connectivity metrics
provider_connectivity = Counter(
    "wasabi_s3_provider_provider_connectivity",
    "Provider connectivity status changes",
    ["provider", "status"],
)

