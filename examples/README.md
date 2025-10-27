# S3 Operator Examples

This directory contains example manifests for using the S3 Provider Operator.

## Quick Start Examples

### Simple Auto-Managed Bucket (Recommended)

The easiest way to create a secure S3 bucket with automatic user, policy, and access key management:

```bash
kubectl apply -f bucket-auto-managed.yaml
```

This creates:
- Provider (with IAM endpoint)
- Bucket (with auto-management enabled)
- User (auto-created)
- BucketPolicy (auto-created)  
- AccessKey (auto-created)
- Secret (credentials stored)

**Get credentials:**
```bash
SECRET_NAME=$(kubectl get bucket my-app-storage -o jsonpath='{.status.credentialsSecret}')
kubectl get secret $SECRET_NAME -o jsonpath='{.data.access-key-id}' | base64 -d
```

See [SIMPLE_WORKFLOW.md](./SIMPLE_WORKFLOW.md) for detailed documentation.

### Basic Bucket (Manual Management)

Create a bucket without auto-management:

```bash
kubectl apply -f bucket-basic.yaml
```

### Provider Examples

- `provider-wasabi.yaml` - Wasabi provider with IAM endpoint

### Access Key Examples

- `accesskey-with-rotation.yaml` - Access key with automatic rotation

### Bucket Policy Examples

- `bucket-policy-public-read.yaml` - Public read access policy

### Lifecycle Rules

- `bucket-with-lifecycle.yaml` - Bucket with lifecycle rules and CORS

## Complete Workflows

### User-Based Workflow

For advanced use cases with explicit user management:

```bash
kubectl apply -f workflow-with-user.yaml
```

See [USER_WORKFLOW.md](./USER_WORKFLOW.md) for step-by-step guide.

### Creating a Single Bucket

**Simplest approach** - Just create a bucket:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Bucket
metadata:
  name: my-storage
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-storage
  autoManage:
    enabled: true
    accessLevel: readwrite
```

Everything else happens automatically!

## Documentation

- [Simple Workflow](./SIMPLE_WORKFLOW.md) - Auto-managed bucket workflow
- [User Workflow](./USER_WORKFLOW.md) - Manual user management workflow

## Testing

All examples are tested and validated:

```bash
# Validate provider
kubectl apply --dry-run=client -f provider-wasabi.yaml

# Validate bucket
kubectl apply --dry-run=client -f bucket-auto-managed.yaml
```

