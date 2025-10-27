# Wasabi S3 Operator Helm Chart

A Kubernetes operator for managing Wasabi S3-compatible storage resources (buckets, users, access keys, and policies) using Custom Resource Definitions (CRDs).

## Quick Start

```bash
# Add the repository
helm repo add wasabi-s3-operator https://kenchrcum.github.io/wasabi-s3-operator
helm repo update

# Install the operator
helm install wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace
```

## Values Reference

The following table lists all configurable values and their default values:

| Parameter | Description | Default |
|-----------|-------------|---------|
| **Image Configuration** |
| `image.repository` | Container image repository | `kenchrcum/wasabi-s3-operator` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `image.tag` | Container image tag | `latest` |
| `imagePullSecrets` | Array of image pull secrets | `[]` |
| **Operator Configuration** |
| `operator.watchScope` | Kubernetes watch scope (`namespaced` or `cluster`) | `namespaced` |
| `operator.logLevel` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `operator.metricsPort` | Port for Prometheus metrics | `8080` |
| **Deployment Configuration** |
| `replicaCount` | Number of operator replicas | `1` |
| `nameOverride` | Override the resource name | `""` |
| `fullnameOverride` | Override the full resource name | `""` |
| **Service Account** |
| `serviceAccount.create` | Whether to create a service account | `true` |
| `serviceAccount.annotations` | Annotations for the service account | `{}` |
| `serviceAccount.name` | Name of the service account (if not using the default) | `""` |
| **RBAC Configuration** |
| `rbac.create` | Whether to create RBAC resources | `true` |
| `rbac.preset` | RBAC permission preset (`minimal`, `scoped`, or `full`) | `minimal` |
| **Resource Limits** |
| `resources.limits.cpu` | CPU limit | `500m` |
| `resources.limits.memory` | Memory limit | `512Mi` |
| `resources.requests.cpu` | CPU request | `100m` |
| `resources.requests.memory` | Memory request | `128Mi` |
| **Security Context** |
| `securityContext.runAsNonRoot` | Run as non-root user | `true` |
| `securityContext.runAsUser` | User ID to run as | `1000` |
| `securityContext.fsGroup` | Filesystem group ID | `1000` |
| **Container Security Context** |
| `containerSecurityContext.allowPrivilegeEscalation` | Allow privilege escalation | `false` |
| `containerSecurityContext.readOnlyRootFilesystem` | Use read-only root filesystem | `true` |
| `containerSecurityContext.capabilities.drop` | List of capabilities to drop | `["ALL"]` |
| **Scheduling** |
| `nodeSelector` | Node selector labels | `{}` |
| `tolerations` | List of tolerations | `[]` |
| `affinity` | Pod affinity rules | `{}` |
| **Service Configuration** |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `8080` |
| **ServiceMonitor (Prometheus)** |
| `serviceMonitor.enabled` | Enable ServiceMonitor for Prometheus | `false` |
| `serviceMonitor.namespace` | Namespace for ServiceMonitor | `""` |
| `serviceMonitor.interval` | Scrape interval | `30s` |
| `serviceMonitor.scrapeTimeout` | Scrape timeout | `10s` |
| `serviceMonitor.labels` | Additional labels for ServiceMonitor | `{}` |
| **Labels and Annotations** |
| `labels` | Additional labels to add to all resources | `{}` |
| `annotations` | Additional annotations to add to all resources | `{}` |

## Configuration Examples

### Basic Installation

```bash
helm install wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace
```

### High Availability Setup

```yaml
# values-ha.yaml
replicaCount: 3

operator:
  watchScope: cluster
  
resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 500m
    memory: 512Mi
```

### Production Configuration

```yaml
# values-production.yaml
image:
  repository: kenchrcum/wasabi-s3-operator
  tag: "v0.1.0"
  pullPolicy: Always

operator:
  watchScope: cluster
  logLevel: INFO
  
resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 500m
    memory: 512Mi

rbac:
  create: true
  preset: full

serviceMonitor:
  enabled: true
  interval: 15s
  scrapeTimeout: 10s
```

### With Custom Image Pull Secrets

```yaml
# values-secure.yaml
imagePullSecrets:
  - name: registry-secret

image:
  repository: registry.example.com/wasabi-s3-operator
  tag: "v0.1.0"
  pullPolicy: Always
```

### Resource-Constrained Environment

```yaml
# values-small.yaml
replicaCount: 1

resources:
  limits:
    cpu: 200m
    memory: 256Mi
  requests:
    cpu: 50m
    memory: 64Mi
```

## Installation

### Install from Repository

```bash
# Add the Helm repository
helm repo add wasabi-s3-operator https://kenchrcum.github.io/wasabi-s3-operator
helm repo update

# Install with default values
helm install wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace

# Install with custom values
helm install wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace \
  -f my-values.yaml
```

### Install from Local Chart

```bash
# Clone the repository
git clone https://github.com/kenchrcum/wasabi-s3-operator.git
cd wasabi-s3-operator

# Install directly from chart
helm install wasabi-s3-operator ./helm/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace
```

## Upgrading

```bash
# Upgrade from repository
helm upgrade wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  -f my-values.yaml

# Upgrade using specific version
helm upgrade wasabi-s3-operator wasabi-s3-operator/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --version 0.1.0
```

## Uninstallation

```bash
# Uninstall the operator
helm uninstall wasabi-s3-operator --namespace wasabi-s3-operator-system

# Delete the namespace (optional)
kubectl delete namespace wasabi-s3-operator-system
```

## Verification

After installation, verify the operator is running:

```bash
# Check deployment
kubectl get deployment -n wasabi-s3-operator-system

# Check pods
kubectl get pods -n wasabi-s3-operator-system

# Check logs
kubectl logs -n wasabi-s3-operator-system -l app.kubernetes.io/name=wasabi-s3-operator -f

# Check CRDs
kubectl get crd | grep s3.cloud37.dev

# Check events
kubectl get events -n wasabi-s3-operator-system --sort-by='.lastTimestamp'
```

## Post-Installation

After the operator is running, create your first resources:

### 1. Create a Provider

```bash
# Create credentials secret
kubectl create secret generic wasabi-credentials \
  --from-literal=access-key=YOUR_ACCESS_KEY \
  --from-literal=secret-key=YOUR_SECRET_KEY

# Create provider
kubectl apply -f examples/provider-wasabi.yaml
```

### 2. Create a Bucket

```bash
kubectl apply -f examples/bucket-basic.yaml
```

### 3. Create a Bucket Policy

```bash
kubectl apply -f examples/bucket-policy-public-read.yaml
```

### 4. Create an Access Key

```bash
kubectl apply -f examples/accesskey-with-rotation.yaml
```

## Monitoring

### Prometheus Integration

Enable ServiceMonitor for Prometheus:

```yaml
serviceMonitor:
  enabled: true
  interval: 30s
  scrapeTimeout: 10s
```

### Available Metrics

The operator exposes the following Prometheus metrics on port 8080:

- `wasabi_s3_provider_reconcile_total{kind,result}` - Total reconciliation counts
- `wasabi_s3_provider_reconcile_duration_seconds{kind}` - Reconciliation duration histogram
- `wasabi_s3_provider_bucket_operations_total{operation,result}` - S3 operation counts
- `wasabi_s3_provider_provider_connectivity{provider}` - Provider connectivity status

Access metrics at: `http://<operator-service>:8080/metrics`

## Troubleshooting

### Operator Not Starting

```bash
# Check pod status
kubectl describe pod -n wasabi-s3-operator-system -l app.kubernetes.io/name=wasabi-s3-operator

# Check logs
kubectl logs -n wasabi-s3-operator-system -l app.kubernetes.io/name=wasabi-s3-operator

# Check events
kubectl get events -n wasabi-s3-operator-system --sort-by='.lastTimestamp'
```

### RBAC Issues

```bash
# Check ClusterRole
kubectl get clusterrole wasabi-s3-operator

# Check ClusterRoleBinding
kubectl get clusterrolebinding wasabi-s3-operator

# Check ServiceAccount
kubectl get serviceaccount -n wasabi-s3-operator-system
```

### CRD Not Created

```bash
# Check CRDs
kubectl get crd providers.s3.cloud37.dev
kubectl get crd buckets.s3.cloud37.dev
kubectl get crd bucketpolicies.s3.cloud37.dev
kubectl get crd accesskeys.s3.cloud37.dev
kubectl get crd users.s3.cloud37.dev

# Describe CRD if issues
kubectl describe crd providers.s3.cloud37.dev
```

### Image Pull Errors

```bash
# Check image pull secrets
kubectl get secrets -n wasabi-s3-operator-system

# Test image access
kubectl run test-pod --image=kenchrcum/wasabi-s3-operator:latest --dry-run=client -o yaml
```

## RBAC Presets

The Helm chart supports three RBAC presets:

- **minimal**: Minimal permissions needed for operator to function
- **scoped**: Scoped permissions for namespace-isolated deployments
- **full**: Full cluster-wide permissions (required for cluster-scoped watching)

Select the appropriate preset based on your deployment:

```yaml
rbac:
  preset: minimal  # or 'scoped' or 'full'
```

## Security Considerations

The default values follow security best practices:

- Run as non-root user (UID 1000)
- Read-only root filesystem
- Drop all capabilities
- No privilege escalation

For production deployments, consider:

- Using image pull secrets for private registries
- Setting resource limits appropriate for your workload
- Enabling Pod Security Standards
- Using network policies to restrict pod-to-pod communication

## Support

- **Documentation**: [README.md](../README.md)
- **Issues**: [GitHub Issues](https://github.com/kenchrcum/wasabi-s3-operator/issues)
- **Examples**: [examples/](../examples/)

## License

Unlicense - See [LICENSE](../LICENSE) file for details.

