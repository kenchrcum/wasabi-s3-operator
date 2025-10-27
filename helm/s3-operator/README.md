# S3 Operator Helm Chart

This Helm chart deploys the S3 Provider Operator to a Kubernetes cluster.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+

## Installation

### Quick Start

```bash
# Add custom values if needed
cat > my-values.yaml <<EOF
image:
  repository: kenchrcum/s3-provider-operator
  tag: "latest"
EOF

# Install the operator
helm install s3-operator ./helm/s3-operator \
  --namespace s3-operator-system \
  --create-namespace \
  -f my-values.yaml
```

### Using Docker Hub Image

```bash
helm install s3-operator ./helm/s3-operator \
  --namespace s3-operator-system \
  --create-namespace \
  --set image.repository=kenchrcum/s3-provider-operator \
  --set image.tag=latest
```

## Configuration

### Image Configuration

```yaml
image:
  repository: kenchrcum/s3-provider-operator
  tag: "latest"
  pullPolicy: IfNotPresent
```

### Operator Configuration

```yaml
operator:
  watchScope: namespaced  # namespaced or cluster
  logLevel: INFO
  metricsPort: 8080
```

### RBAC Configuration

```yaml
rbac:
  create: true
  preset: minimal  # minimal, scoped, or full
```

### Resource Limits

```yaml
resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 100m
    memory: 128Mi
```

### Service Monitor (Prometheus)

```yaml
serviceMonitor:
  enabled: false
  namespace: ""
  interval: 30s
  scrapeTimeout: 10s
```

## Uninstallation

```bash
helm uninstall s3-operator --namespace s3-operator-system
```

## Verification

After installation, verify the operator is running:

```bash
# Check deployment
kubectl get deployment -n s3-operator-system

# Check pods
kubectl get pods -n s3-operator-system

# Check logs
kubectl logs -n s3-operator-system -l app.kubernetes.io/name=s3-operator

# Check CRDs
kubectl get crd | grep s3.cloud37.dev
```

## Post-Installation

After the operator is running, you can create S3 resources:

1. Create a Provider (see `examples/provider-wasabi.yaml`)
2. Create a Bucket (see `examples/bucket-basic.yaml`)
3. Create a BucketPolicy (see `examples/bucket-policy-public-read.yaml`)
4. Create an AccessKey (see `examples/accesskey-with-rotation.yaml`)

## Troubleshooting

### Operator Not Starting

```bash
# Check pod logs
kubectl logs -n s3-operator-system -l app.kubernetes.io/name=s3-operator

# Check events
kubectl get events -n s3-operator-system --sort-by='.lastTimestamp'
```

### RBAC Issues

```bash
# Check ClusterRoleBinding
kubectl get clusterrolebinding | grep s3-operator

# Check ServiceAccount
kubectl get serviceaccount -n s3-operator-system
```

### CRD Not Created

```bash
# Check CRDs
kubectl get crd providers.s3.cloud37.dev
kubectl get crd buckets.s3.cloud37.dev
kubectl get crd bucketpolicies.s3.cloud37.dev
kubectl get crd accesskeys.s3.cloud37.dev
```

## Values Reference

See `values.yaml` for all available configuration options.

