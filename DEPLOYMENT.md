# Wasabi S3 Provider Deployment Guide

This guide walks you through building and deploying the Wasabi S3 Provider Operator to a Kubernetes cluster.

## Prerequisites

- Docker installed and running
- Kubernetes cluster (local or remote)
- `kubectl` configured to access your cluster
- Helm 3.8+ installed
- Docker Hub account (or registry of your choice)

## Step 1: Build the Docker Image

Build the operator Docker image:

```bash
# Build the image
docker build -t kenchrcum/wasabi-s3-provider:latest .

# Or use the build script
./scripts/build-and-push.sh latest
```

## Step 2: Push to Registry

Push the image to Docker Hub (or your preferred registry):

```bash
# Login to Docker Hub
docker login

# Push the image
docker push kenchrcum/wasabi-s3-provider:latest
```

## Step 3: Install the Operator

Install the operator using Helm:

```bash
# Create namespace
kubectl create namespace wasabi-s3-provider-system

# Install the operator
helm install wasabi-s3-provider ./helm/wasabi-s3-provider \
  --namespace wasabi-s3-provider-system \
  --set image.repository=kenchrcum/wasabi-s3-provider \
  --set image.tag=latest
```

### With Custom Values

Create a `my-values.yaml` file:

```yaml
image:
  repository: kenchrcum/wasabi-s3-provider
  tag: "latest"

operator:
  watchScope: namespaced
  logLevel: INFO

resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 100m
    memory: 128Mi
```

Then install:

```bash
helm install wasabi-s3-provider ./helm/wasabi-s3-provider \
  --namespace wasabi-s3-provider-system \
  -f my-values.yaml
```

## Step 4: Verify Installation

Check that the operator is running:

```bash
# Check deployment
kubectl get deployment -n wasabi-s3-provider-system

# Check pods
kubectl get pods -n wasabi-s3-provider-system

# Check CRDs
kubectl get crd | grep s3.cloud37.dev

# Check logs
kubectl logs -n wasabi-s3-provider-system -l app.kubernetes.io/name=wasabi-s3-provider --tail=50
```

Expected output should show:
- Deployment running
- Pod in `Running` state
- 4 CRDs installed (providers, buckets, bucketpolicies, accesskeys)
- No errors in logs

## Step 5: Create Your First Provider

Create a Provider resource (example with Wasabi):

```bash
# Create a secret with credentials
kubectl create secret generic wasabi-credentials \
  --from-literal=access-key=YOUR_ACCESS_KEY \
  --from-literal=secret-key=YOUR_SECRET_KEY \
  --namespace default

# Create the Provider
kubectl apply -f examples/provider-wasabi.yaml
```

Check the Provider status:

```bash
kubectl get provider -n default
kubectl describe provider wasabi-us-east-1 -n default
```

Wait for the Provider to be ready (check conditions):

```bash
kubectl get provider wasabi-us-east-1 -n default -o jsonpath='{.status.conditions}' | jq
```

## Step 6: Create Your First Bucket

Create a Bucket resource:

```bash
kubectl apply -f examples/bucket-basic.yaml
```

Check the Bucket status:

```bash
kubectl get bucket -n default
kubectl describe bucket my-backup-bucket -n default
```

## Step 7: Create a Bucket Policy

Create a BucketPolicy:

```bash
kubectl apply -f examples/bucket-policy-public-read.yaml
```

Check the policy status:

```bash
kubectl get bucketpolicy -n default
```

## Step 8: Monitor the Operator

### View Logs

```bash
# Follow logs
kubectl logs -n wasabi-s3-provider-system -l app.kubernetes.io/name=wasabi-s3-provider -f

# Check specific resource logs
kubectl logs -n wasabi-s3-provider-system deployment/wasabi-s3-provider
```

### View Events

```bash
# Operator events
kubectl get events -n wasabi-s3-provider-system --sort-by='.lastTimestamp'

# Resource events
kubectl get events -n default --field-selector involvedObject.kind=Provider
```

### View Metrics

If Prometheus is configured, metrics are available at:

```
http://<operator-service>:8080/metrics
```

## Troubleshooting

### Pod Not Starting

```bash
# Check pod status
kubectl describe pod -n wasabi-s3-provider-system -l app.kubernetes.io/name=wasabi-s3-provider

# Check logs
kubectl logs -n wasabi-s3-provider-system -l app.kubernetes.io/name=wasabi-s3-provider
```

### Provider Not Ready

```bash
# Check Provider conditions
kubectl get provider wasabi-us-east-1 -n default -o yaml

# Check for authentication errors
kubectl logs -n wasabi-s3-provider-system -l app.kubernetes.io/name=wasabi-s3-provider | grep -i auth
```

### Bucket Creation Failing

```bash
# Check Bucket conditions
kubectl get bucket -n default -o yaml

# Check events
kubectl get events -n default --field-selector involvedObject.kind=Bucket
```

### RBAC Issues

```bash
# Check ClusterRoleBinding
kubectl get clusterrolebinding | grep wasabi-s3-provider

# Check ServiceAccount
kubectl get serviceaccount -n wasabi-s3-provider-system
```

## Uninstallation

To remove the operator:

```bash
# Uninstall Helm release
helm uninstall wasabi-s3-provider --namespace wasabi-s3-provider-system

# Delete namespace
kubectl delete namespace wasabi-s3-provider-system

# Optional: Delete CRDs (this will delete all custom resources)
kubectl delete crd providers.s3.cloud37.dev buckets.s3.cloud37.dev bucketpolicies.s3.cloud37.dev accesskeys.s3.cloud37.dev
```

## Local Development

For local development with kubectl proxy:

```bash
# Run operator locally
source .venv/bin/activate
python -m wasabi_s3_provider.main

# In another terminal, proxy to cluster
kubectl proxy

# Operator will connect via the proxy
```

## Next Steps

- Read the [Development Status](./architecture/STATUS.md)
- Explore [CRD Specifications](./architecture/crd-specifications.md)
- Check [Examples](./examples/) for more use cases
- Review [Development Plan](./architecture/development-plan.md)

