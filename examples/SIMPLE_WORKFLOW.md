# Simple Bucket Creation Workflow

Create an S3 bucket with automatic user, policy, and access key management in just **2 steps**!

## Quick Start

### 1. Create Provider

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Provider
metadata:
  name: wasabi-us-east-1
spec:
  type: wasabi
  endpoint: https://s3.wasabisys.com
  iamEndpoint: https://iam.wasabisys.com  # Required!
  region: us-east-1
  auth:
    accessKeySecretRef:
      name: wasabi-credentials
      key: access-key
    secretKeySecretRef:
      name: wasabi-credentials
      key: secret-key
```

### 2. Create Bucket

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Bucket
metadata:
  name: my-app-storage
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-app-storage
  autoManage:
    enabled: true              # Enable automatic management
    userName: my-app-user      # Optional: defaults to bucket name
    accessLevel: readwrite     # readonly, readwrite, or full
    rotation:
      enabled: true
      intervalDays: 90
```

That's it! The operator automatically creates:
- ✅ **User** (`my-app-storage-user`)
- ✅ **BucketPolicy** (grants user access)
- ✅ **AccessKey** (creates credentials)
- ✅ **Secret** (stores credentials)

## Get Credentials

After the bucket is ready, get credentials from the Secret:

```bash
# Get secret name from bucket status
SECRET_NAME=$(kubectl get bucket my-app-storage -o jsonpath='{.status.credentialsSecret}')

# Or use the default pattern
SECRET_NAME="my-app-storage-accesskey-credentials"

# Retrieve credentials
kubectl get secret $SECRET_NAME -o jsonpath='{.data.access-key-id}' | base64 -d
kubectl get secret $SECRET_NAME -o jsonpath='{.data.secret-access-key}' | base64 -d
```

## Configuration Options

### Access Levels

```yaml
autoManage:
  accessLevel: readonly   # Only GetObject and ListBucket
  accessLevel: readwrite  # GetObject, PutObject, DeleteObject, ListBucket
  accessLevel: full       # All S3 operations (s3:*)
```

### Disable Auto-Management

To manage resources manually:

```yaml
spec:
  autoManage:
    enabled: false
```

### Custom User Name

```yaml
spec:
  autoManage:
    userName: custom-user-name
```

## View Created Resources

```bash
# List all resources
kubectl get bucket,user,bucketpolicy,accesskey

# Check bucket status
kubectl get bucket my-app-storage -o yaml

# View auto-created user
kubectl get user my-app-storage-user

# View bucket policy
kubectl get bucketpolicy my-app-storage-policy

# View access key
kubectl get accesskey my-app-storage-accesskey
```

## Cleanup

Deleting the bucket automatically deletes all related resources:

```bash
kubectl delete bucket my-app-storage
```

This cascade deletes (via owner references):
- User
- BucketPolicy
- AccessKey
- Secret

## Complete Example

See `bucket-auto-managed.yaml` for a complete working example.

## Troubleshooting

### Bucket Not Ready

```bash
kubectl describe bucket my-app-storage
kubectl get events --field-selector involvedObject.kind=Bucket
```

### User Creation Failed

```bash
kubectl describe user my-app-storage-user
# Check that Provider has iamEndpoint configured
```

### Access Key Not Created

```bash
kubectl describe accesskey my-app-storage-accesskey
# Ensure User is Ready before AccessKey can be created
```

### Missing Credentials Secret

```bash
# Check if secret exists
kubectl get secret my-app-storage-accesskey-credentials

# Check access key status
kubectl get accesskey my-app-storage-accesskey -o yaml
```

## Benefits

✅ **One Resource** - Only create a Bucket  
✅ **Automatic** - User, Policy, and Keys created automatically  
✅ **Secure** - Credentials stored in Kubernetes Secrets  
✅ **Managed** - Owner references ensure cleanup  
✅ **Flexible** - Configurable access levels  
✅ **Rotatable** - Automatic key rotation support  

