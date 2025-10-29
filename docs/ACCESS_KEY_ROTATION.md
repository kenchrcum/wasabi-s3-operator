# Access Key Rotation

The Wasabi S3 Operator supports automatic access key rotation for enhanced security. This feature allows you to automatically rotate access keys at regular intervals while maintaining seamless operation.

## Overview

Access key rotation is a security best practice that regularly changes the credentials used to authenticate with your S3 provider. The operator handles this automatically, creating new keys, updating Kubernetes secrets, and cleaning up old keys according to your configured retention policy.

## Features

- **Automatic Rotation**: Keys are rotated automatically based on a configurable interval
- **Seamless Updates**: Kubernetes secrets are updated without service interruption
- **Retention Management**: Previous keys are retained for a configurable period to ensure continuity
- **Automatic Cleanup**: Expired keys are automatically deleted from the provider

## Configuration

Access key rotation is configured in the `AccessKey` CRD using the `rotate` section:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: AccessKey
metadata:
  name: application-key
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:
    name: application-user
  displayName: Application Service Key
  rotate:
    enabled: true
    intervalDays: 90              # Rotate every 90 days
    previousKeysRetentionDays: 7 # Keep old keys for 7 days
```

### Configuration Options

- **`enabled`** (boolean, default: `false`): Enable automatic rotation
- **`intervalDays`** (integer, default: `90`, range: 1-365): Number of days between rotations
- **`previousKeysRetentionDays`** (integer, default: `7`, range: 0-30): Number of days to retain previous keys before deletion

## How It Works

### Initial Creation

When an AccessKey is first created with rotation enabled:

1. A new access key is created in Wasabi
2. The credentials are stored in a Kubernetes secret
3. The `lastRotateTime` and `nextRotateTime` are set in the status
4. The operator schedules the next rotation based on `intervalDays`

### Rotation Process

When the `nextRotateTime` is reached:

1. **Create New Key**: A new access key is created for the user
2. **Update Secret**: The Kubernetes secret is updated with the new credentials
3. **Track Previous Key**: The old key is added to the previous keys list with a timestamp
4. **Update Status**: 
   - `accessKeyId` is updated to the new key
   - `lastRotateTime` is set to the current time
   - `nextRotateTime` is set to the next rotation time
5. **Cleanup**: Old keys are deleted from Wasabi after the retention period

### Key Tracking

Previous keys are tracked in the AccessKey resource's annotations as JSON:

```json
[
  {
    "accessKeyId": "AKIA...",
    "rotatedAt": "2024-01-01T00:00:00+00:00"
  }
]
```

### Automatic Cleanup

During each reconciliation, the operator:

1. Checks all previous keys for expiration
2. Deletes keys older than `previousKeysRetentionDays` from Wasabi
3. Removes expired keys from the tracking list
4. Updates the resource annotations

## Status Fields

The AccessKey status includes rotation-related fields:

```yaml
status:
  accessKeyId: "AKIA1234567890"
  created: true
  lastRotateTime: "2024-01-01T00:00:00+00:00"
  nextRotateTime: "2024-04-01T00:00:00+00:00"
  conditions:
    - type: Ready
      status: "True"
      message: "Access key AKIA1234567890 is ready"
```

## Events

The operator emits Kubernetes events for rotation activities:

- **`AccessKeyRotated`**: Emitted when a key is successfully rotated
- **`RotationFailed`**: Emitted when rotation fails (condition: `RotationFailed`)

## Best Practices

### Rotation Intervals

- **Short Interval (30 days)**: For high-security environments or compliance requirements
- **Medium Interval (90 days)**: Default recommendation for most use cases
- **Long Interval (180-365 days)**: For low-risk scenarios with stable applications

### Retention Periods

- **Short Retention (0-3 days)**: For automated systems that can quickly adapt to new credentials
- **Medium Retention (7 days)**: Default recommendation, balances security and continuity
- **Long Retention (14-30 days)**: For systems that need extended transition periods

### Monitoring

Monitor the AccessKey resource status to ensure rotation is working:

```bash
# Check rotation status
kubectl get accesskey application-key -o yaml

# Watch for rotation events
kubectl get events --field-selector involvedObject.name=application-key --watch

# Check rotation schedule
kubectl get accesskey application-key -o jsonpath='{.status.nextRotateTime}'
```

## Example Use Cases

### Production Application

Rotate keys every 90 days with 7-day retention:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: AccessKey
metadata:
  name: production-api-key
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:
    name: production-api-user
  rotate:
    enabled: true
    intervalDays: 90
    previousKeysRetentionDays: 7
```

### High-Security Environment

Rotate keys every 30 days with immediate cleanup:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: AccessKey
metadata:
  name: secure-service-key
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:
    name: secure-service-user
  rotate:
    enabled: true
    intervalDays: 30
    previousKeysRetentionDays: 0
```

### Development Environment

Disable rotation for convenience:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: AccessKey
metadata:
  name: dev-service-key
spec:
  providerRef:
    name: wasabi-us-east-1
  userRef:
    name: dev-service-user
  rotate:
    enabled: false
```

## Troubleshooting

### Rotation Not Happening

Check if rotation is enabled and scheduled:

```bash
kubectl get accesskey <name> -o jsonpath='{.spec.rotate.enabled}'
kubectl get accesskey <name> -o jsonpath='{.status.nextRotateTime}'
```

### Rotation Failed

Check conditions and events:

```bash
kubectl describe accesskey <name>
kubectl get events --field-selector involvedObject.name=<name>
```

Common reasons for rotation failure:
- Provider connectivity issues
- IAM permissions insufficient
- User resource not ready
- Rate limiting from Wasabi

### Old Keys Not Being Deleted

Check the retention configuration and annotations:

```bash
kubectl get accesskey <name> -o jsonpath='{.spec.rotate.previousKeysRetentionDays}'
kubectl get accesskey <name> -o jsonpath='{.metadata.annotations.s3\.cloud37\.dev/previous-keys}'
```

## Security Considerations

1. **Minimize Exposure**: Use shorter retention periods in sensitive environments
2. **Monitor Rotation**: Set up alerts for rotation failures
3. **Audit Logs**: Regularly review rotation events in your monitoring system
4. **Rate Limits**: Be aware of Wasabi's rate limits for IAM operations
5. **Backup Keys**: Never disable rotation entirely for production workloads

## Limitations

- Maximum rotation interval: 365 days
- Maximum retention period: 30 days
- Previous keys are limited by retention period (not unlimited storage)
- Rotation time is determined at creation, not dynamically adjustable
- Manual rotation is not currently supported (only automatic)

## Future Enhancements

Potential improvements planned:
- Manual rotation trigger via annotation
- Custom rotation schedules (specific dates/times)
- Pre-rotation notification hooks
- Integration with secret rotation frameworks
- Rotation based on usage patterns

