# User-Based Workflow

This guide demonstrates the complete IAM-based workflow for secure bucket access.

## Workflow Overview

The recommended flow for creating a secure S3 bucket with dedicated credentials:

1. **Provider** - Configure S3 provider with IAM endpoint
2. **User** - Create an IAM user
3. **Bucket** - Create bucket with user reference
4. **BucketPolicy** - Grant user access to bucket
5. **AccessKey** - Create access keys for the user
6. **Secret** - Credentials stored in Kubernetes Secret

## Complete Example

See `workflow-with-user.yaml` for a complete example of all resources.

## Step-by-Step

### 1. Create Provider with IAM Endpoint

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Provider
metadata:
  name: wasabi-us-east-1
spec:
  type: wasabi
  endpoint: https://s3.wasabisys.com
  iamEndpoint: https://iam.wasabisys.com  # Required for user management
  region: us-east-1
  auth:
    accessKeySecretRef:
      name: wasabi-credentials
      key: access-key
    secretKeySecretRef:
      name: wasabi-credentials
      key: secret-key
```

### 2. Create User

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: User
metadata:
  name: my-app-user
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-app-user
  tags:
    Environment: production
```

Wait for user to be ready:
```bash
kubectl wait --for=condition=Ready user/my-app-user --timeout=60s
```

### 3. Create Bucket with User Reference

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Bucket
metadata:
  name: my-app-bucket
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:  # Optional: references the user
    name: my-app-user
  name: my-app-bucket
  versioning:
    enabled: true
```

### 4. Create Bucket Policy

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: BucketPolicy
metadata:
  name: my-app-bucket-policy
spec:
  bucketRef:
    name: my-app-bucket
  policy:
    version: "2012-10-17"
    statement:
      - effect: Allow
        principal: "arn:aws:iam::wasabi:user/my-app-user"
        action:
          - s3:GetObject
          - s3:PutObject
        resource:
          - "arn:aws:s3:::my-app-bucket/*"
```

### 5. Create Access Keys for User

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: AccessKey
metadata:
  name: my-app-access-key
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:  # Required: specifies which user to create keys for
    name: my-app-user
  rotate:
    enabled: true
    intervalDays: 90
```

### 6. Retrieve Credentials

After the AccessKey is created, credentials are stored in a Kubernetes Secret:

```bash
# Get the secret name
SECRET_NAME=$(kubectl get accesskey my-app-access-key -o jsonpath='{.metadata.name}')-credentials

# Retrieve credentials
kubectl get secret $SECRET_NAME -o jsonpath='{.data.access-key-id}' | base64 -d
kubectl get secret $SECRET_NAME -o jsonpath='{.data.secret-access-key}' | base64 -d
```

## Benefits

✅ **Least Privilege** - User only has access to specific bucket  
✅ **Separation of Concerns** - Each application has its own user  
✅ **Automatic Rotation** - Access keys can be rotated automatically  
✅ **Secure Storage** - Credentials stored in Kubernetes Secrets  
✅ **Audit Trail** - IAM user tracks all access  

## Alternative: Direct Access (Without User)

For simpler use cases without IAM, you can still create buckets and access keys without user management:

```yaml
# Bucket without userRef
apiVersion: s3.cloud37.dev/v1alpha1
kind: Bucket
metadata:
  name: simple-bucket
spec:
  providerRef:
    name: wasabi-us-east-1
  name: simple-bucket

# AccessKey without userRef (not recommended)
# This requires direct API access without IAM user
```

Note: Without `userRef`, AccessKey creation may not work with all providers that require IAM.

## Troubleshooting

### User Not Ready

```bash
kubectl describe user my-app-user
kubectl get events --field-selector involvedObject.kind=User
```

### Access Key Creation Failed

```bash
kubectl describe accesskey my-app-access-key
# Check that userRef is provided and user is ready
```

### Bucket Policy Not Applied

```bash
kubectl describe bucketpolicy my-app-bucket-policy
# Verify bucket exists and policy document is valid
```

