# IAM Policy Management

## Overview

The IAMPolicy CRD enables reusable IAM policy management for Wasabi S3 users. Policies can be defined once and attached to multiple users, promoting consistency and reducing duplication.

## Features

- **Reusable Policies**: Define IAM policies once and reference them from multiple users
- **Policy Validation**: Automatic validation of policy documents against IAM standards
- **Status Tracking**: Track which users have policies attached
- **Mutual Exclusivity**: Users can either have an inline policy OR reference an IAMPolicy (not both)

## CRD Specifications

### IAMPolicy Spec

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: IAMPolicy
metadata:
  name: bucket-readwrite-policy
spec:
  providerRef:
    name: wasabi-us-east-1
    namespace: default  # optional
  policy:
    version: "2012-10-17"
    statement:
      - effect: Allow
        action:
          - s3:GetObject
          - s3:PutObject
          - s3:DeleteObject
          - s3:ListBucket
        resource:
          - "arn:aws:s3:::my-bucket"
          - "arn:aws:s3:::my-bucket/*"
  tags:
    Environment: production
```

### IAMPolicy Status

```yaml
status:
  observedGeneration: 1
  policyArn: "arn:aws:iam::*:policy/bucket-readwrite-policy"
  applied: true
  attachedUsers: []
  lastSyncTime: "2024-01-01T00:00:00Z"
  conditions:
    - type: Ready
      status: "True"
      reason: "Ready"
      message: "IAMPolicy bucket-readwrite-policy is ready"
```

## User Integration

### Using Inline Policy (Existing Behavior)

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: User
metadata:
  name: my-app-user
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-app-user
  policy:  # Inline policy
    version: "2012-10-17"
    statement:
      - effect: Allow
        action: ["s3:GetObject", "s3:PutObject"]
        resource: ["arn:aws:s3:::my-bucket/*"]
```

### Using Policy Reference (New Feature)

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: User
metadata:
  name: my-app-user
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-app-user
  policyRef:  # Reference to IAMPolicy
    name: bucket-readwrite-policy
    namespace: default  # optional
```

## Important Notes

### 1. Mutual Exclusivity

Users cannot specify both `policy` and `policyRef` simultaneously. The operator will reject such configurations.

### 2. Bucket Auto-Management

IAMPolicy is **NOT** used in the `autoManage` feature of buckets. Buckets with `autoManage.enabled: true` will continue to use inline policies embedded in the User CRD they create.

### 3. Policy Attachment

- IAMPolicy CRDs are validated and stored when created
- When a User references an IAMPolicy via `policyRef`, the policy content is retrieved and attached as an inline policy to the IAM user
- The actual implementation uses IAM inline policies (not managed policies) for maximum compatibility with Wasabi

## Usage Examples

### Example 1: Basic IAMPolicy

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: IAMPolicy
metadata:
  name: bucket-readwrite-policy
spec:
  providerRef:
    name: wasabi-us-east-1
  policy:
    version: "2012-10-17"
    statement:
      - effect: Allow
        action:
          - s3:GetObject
          - s3:PutObject
          - s3:DeleteObject
          - s3:ListBucket
        resource:
          - "arn:aws:s3:::my-bucket"
          - "arn:aws:s3:::my-bucket/*"
```

### Example 2: User with Policy Reference

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: User
metadata:
  name: my-app-user
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-app-user
  policyRef:
    name: bucket-readwrite-policy
```

### Example 3: Complete Workflow

See `examples/workflow-with-iampolicy.yaml` for a complete example including:
1. Creating an IAMPolicy
2. Creating a User that references the policy
3. Creating an AccessKey for the user

## Benefits

1. **Reusability**: Define policies once, use many times
2. **Consistency**: Ensure all users have identical permissions
3. **Maintainability**: Update a policy in one place
4. **Flexibility**: Support both inline and referenced policies
5. **Validation**: Centralized policy validation

## Implementation Details

### Policy Conversion

The operator automatically converts the CRD policy format (lowercase keys) to AWS IAM format (PascalCase keys):

- `version` → `Version`
- `statement` → `Statement`
- `effect` → `Effect`
- `action` → `Action`
- `resource` → `Resource`
- `principal` → `Principal`
- `condition` → `Condition`

### Status Conditions

IAMPolicy supports the following conditions:

- `Ready`: Policy is valid and ready to be attached
- `ProviderNotReady`: Provider dependency is not ready
- `CreationFailed`: Failed to create the policy

### Metrics

IAMPolicy reconciliation metrics are tracked:

- `wasabi_s3_operator_reconcile_total{kind="IAMPolicy", result=...}`
- `wasabi_s3_operator_reconcile_duration_seconds{kind="IAMPolicy"}`

## Troubleshooting

### Policy Not Attaching

1. Check that the IAMPolicy is in Ready state
2. Verify the User spec has either `policy` OR `policyRef` (not both)
3. Ensure the provider has IAM endpoint configured
4. Check operator logs for detailed error messages

### Policy Validation Errors

- Ensure policy follows IAM JSON policy document structure
- Verify action names use correct S3 action prefixes (e.g., `s3:GetObject`)
- Check that resource ARNs are properly formatted

## Future Enhancements

Potential future improvements:

1. Managed policy support (not just inline policies)
2. Policy versioning and history
3. Policy drift detection
4. Cross-region policy replication
5. Policy templates with variables

