# Wasabi S3 Operator Examples

This directory contains example manifests for using the Wasabi S3 Operator. All examples are tested and validated for use with Wasabi's S3-compatible API.

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

## Individual Resource Examples

### Provider
- `provider-wasabi.yaml` - Wasabi provider with IAM endpoint

### Bucket
- `bucket-basic.yaml` - Basic bucket without auto-management
- `bucket-auto-managed.yaml` - Bucket with automatic user/policy/key creation
- `bucket-with-lifecycle.yaml` - Bucket with lifecycle rules and CORS
- `bucket-with-deletion.yaml` - Bucket with deletion protection

### Bucket Policy
- `bucket-policy-public-read.yaml` - Public read access policy

### User
- `user-basic.yaml` - Basic IAM user
- `user-with-iampolicy.yaml` - User with IAMPolicy reference

### IAM Policy
- `iampolicy-basic.yaml` - Reusable IAM policy

### Access Key
- `accesskey-with-rotation.yaml` - Access key with automatic rotation

### Complete Workflows
- `workflow-with-user.yaml` - Complete workflow with explicit user management
- `workflow-with-iampolicy.yaml` - Complete workflow using IAMPolicy

See workflow documentation:
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

