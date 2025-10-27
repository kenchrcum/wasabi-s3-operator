# S3 Operator Quick Start

Get the S3 Provider Operator up and running in minutes!

## Prerequisites

- Docker
- Kubernetes cluster
- kubectl configured
- Helm 3.8+

## Quick Deploy

### Option 1: Automated Script

```bash
# Build, push, and deploy
./scripts/quick-deploy.sh latest
```

### Option 2: Manual Steps

```bash
# 1. Build the image
docker build -t kenchrcum/s3-provider-operator:latest .

# 2. Push to registry (optional)
docker push kenchrcum/s3-provider-operator:latest

# 3. Install with Helm
helm install s3-operator ./helm/s3-operator \
  --namespace s3-operator-system \
  --create-namespace \
  --set image.repository=kenchrcum/s3-provider-operator

# 4. Verify
kubectl get pods -n s3-operator-system
```

## Create Your First Resources

### 1. Create a Provider

```bash
# Create credentials secret
kubectl create secret generic wasabi-credentials \
  --from-literal=access-key=YOUR_KEY \
  --from-literal=secret-key=YOUR_SECRET

# Apply provider manifest
kubectl apply -f examples/provider-wasabi.yaml

# Wait for ready
kubectl wait --for=condition=Ready provider/wasabi-us-east-1 --timeout=60s
```

### 2. Create a Bucket

```bash
kubectl apply -f examples/bucket-basic.yaml

# Check status
kubectl get bucket my-backup-bucket
```

### 3. Create a Bucket Policy

```bash
kubectl apply -f examples/bucket-policy-public-read.yaml

# Check status
kubectl get bucketpolicy public-read-policy
```

### 4. Create an Access Key

```bash
kubectl apply -f examples/accesskey-with-rotation.yaml

# Get the access key secret
kubectl get secret application-key-credentials -o jsonpath='{.data.access-key-id}' | base64 -d
```

## Monitor Your Resources

```bash
# Watch all S3 resources
watch kubectl get providers,buckets,bucketpolicies,accesskeys

# Check operator logs
kubectl logs -n s3-operator-system -l app.kubernetes.io/name=s3-operator -f

# View events
kubectl get events --sort-by='.lastTimestamp'
```

## Troubleshooting

### Operator Not Running

```bash
kubectl logs -n s3-operator-system -l app.kubernetes.io/name=s3-operator
```

### Provider Not Ready

```bash
kubectl describe provider wasabi-us-east-1
```

### Bucket Creation Failed

```bash
kubectl describe bucket my-backup-bucket
kubectl get events --field-selector involvedObject.kind=Bucket
```

## Clean Up

```bash
# Delete all resources
kubectl delete -f examples/

# Uninstall operator
helm uninstall s3-operator --namespace s3-operator-system
```

## Next Steps

- Read the [Deployment Guide](./DEPLOYMENT.md) for detailed instructions
- Check [Development Status](./architecture/STATUS.md) for current progress
- Explore [CRD Specifications](./architecture/crd-specifications.md)

