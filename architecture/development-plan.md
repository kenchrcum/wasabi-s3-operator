# S3 Provider Operator Development Plan (v1alpha1)

This document defines a step-by-step plan to build a Kubernetes Operator for managing S3-compatible storage providers (Wasabi, AWS S3, MinIO, etc.) using the Kopf framework in Python. The operator will provide declarative management of buckets, bucket policies, and access keys across multiple S3 providers.

- API Group: `s3.cloud37.dev`
- Initial Version: `v1alpha1` (evolve to `v1beta1`/`v1` with conversion)
- Scope: Provider, Bucket, BucketPolicy, AccessKey CRDs with provider-agnostic abstraction
- Non-goals (for v1alpha1): complex replication, cross-region failover, advanced analytics

## Phase 1: Project Setup and Design

### 1. Repository Structure
- Layout: `src/` (controllers, services), `schemas/` (CRD definitions), `helm/` (chart), `docs/`, `tests/`, `examples/`, `scripts/` (dev tooling)
- Tooling: Python 3.14, `ruff`, `black`, `mypy`, `pytest`, `pre-commit`; dependency management via `uv` or `pip-tools`
- Operator runtime: enable leader election; watch scope configurable (namespace/cluster-wide)
- Conventions: Conventional Commits; PRs require relevant tests + docs

### 2. API Definition and Conventions
- CRDs are structural with defaults, validation, and `status` subresource enabled
- Use Conditions with `type`, `status`, `reason`, `message`, `lastTransitionTime`
- Use OwnerReferences for derived resources; use Finalizers for cleanup
- Server-Side Apply with a single field manager (e.g., `wasabi-s3-operator`) to manage owned fields

## Phase 2: CRDs (Custom Resource Definitions)

All references are namespaced unless explicitly noted. Fields listed with defaults are optional.

### A. Provider
Represents an S3-compatible storage provider and its authentication configuration.

Spec:
- `type` (enum, required): `wasabi`, `aws`, `minio`, `custom`
- `endpoint` (string, required): Provider API endpoint URL
- `region` (string, required): Provider region (e.g., `us-east-1`, `us-west-1`)
- `auth` (object, required):
  - `accessKeySecretRef` (object): `name`, `key` (default: `access-key`)
  - `secretKeySecretRef` (object): `name`, `key` (default: `secret-key`)
  - `sessionTokenSecretRef` (object, optional): For temporary credentials
- `tls` (object, optional):
  - `insecureSkipVerify` (bool, default: false): Skip TLS verification
  - `caCertSecretRef` (object, optional): CA certificate for custom TLS
- `pathStyle` (bool, default: true): Use path-style addressing (required for Wasabi)
- `retry` (object, optional):
  - `maxAttempts` (int, default: 3)
  - `backoffStrategy` (enum: `exponential|linear`, default: `exponential`)

Status:
- `observedGeneration` (int)
- `connected` (bool): Connectivity status
- `lastConnectTime` (timestamp)
- `conditions`: `AuthValid`, `EndpointReachable`, `Ready`

### B. Bucket
Represents an S3 bucket managed by the operator.

Spec:
- `providerRef` (object, required): `name` (and optional `namespace` if cross-namespace allowed)
- `name` (string, required): Bucket name (must be DNS-compliant)
- `region` (string, optional): Override provider region
- `versioning` (object, optional):
  - `enabled` (bool, default: false)
  - `mfaDelete` (bool, default: false): Require MFA for delete operations
- `encryption` (object, optional):
  - `enabled` (bool, default: false)
  - `algorithm` (enum: `AES256|aws:kms`, default: `AES256`)
  - `kmsKeyId` (string, optional): Required when algorithm is `aws:kms`
- `publicAccess` (object, optional):
  - `blockPublicAcls` (bool, default: true)
  - `blockPublicPolicy` (bool, default: true)
  - `ignorePublicAcls` (bool, default: true)
  - `restrictPublicBuckets` (bool, default: true)
- `lifecycle` (object, optional):
  - `rules` (array): List of lifecycle rules
    - `id` (string, required): Unique rule identifier
    - `status` (enum: `Enabled|Disabled`, default: `Enabled`)
    - `prefix` (string, optional): Apply rule to objects with this prefix
    - `expiration` (object, optional):
      - `days` (int): Delete objects after N days
      - `date` (string): Delete objects on specific date (ISO 8601)
    - `transitions` (array, optional): List of transition rules
      - `days` (int): Transition after N days
      - `storageClass` (string): Target storage class
- `cors` (object, optional):
  - `rules` (array): List of CORS rules
    - `allowedOrigins` (array): List of allowed origins
    - `allowedMethods` (array): List of allowed HTTP methods
    - `allowedHeaders` (array): List of allowed headers
    - `exposedHeaders` (array): List of exposed headers
    - `maxAgeSeconds` (int): Cache max age
- `notifications` (object, optional):
  - `lambda` (array): Lambda function configurations
  - `sqs` (array): SQS queue configurations
  - `sns` (array): SNS topic configurations
- `tagging` (object, optional):
  - `tags` (map[string]string): Key-value tags

Status:
- `observedGeneration` (int)
- `bucketName` (string): Actual bucket name (may differ from spec due to provider constraints)
- `arn` (string): Bucket ARN (if available)
- `exists` (bool): Whether bucket exists in provider
- `lastSyncTime` (timestamp)
- `conditions`: `Ready`, `ProviderNotReady`, `CreationFailed`, `PolicyConflict`

### C. BucketPolicy
Represents an IAM-style bucket policy document.

Spec:
- `bucketRef` (object, required): `name` (and optional `namespace`)
- `policy` (object, required): IAM policy document (JSON)
  - `version` (string, default: "2012-10-17")
  - `statement` (array): List of policy statements
    - `sid` (string, optional): Statement ID
    - `effect` (enum: `Allow|Deny`, required)
    - `principal` (object or string, required): Principal(s) to apply policy to
    - `action` (array or string, required): Action(s) to allow/deny
    - `resource` (array or string, required): Resource ARN(s)
    - `condition` (object, optional): Condition block

Status:
- `observedGeneration` (int)
- `applied` (bool): Whether policy is applied to bucket
- `lastSyncTime` (timestamp)
- `conditions`: `Ready`, `BucketNotReady`, `PolicyInvalid`, `ApplyFailed`

### D. AccessKey
Represents an access key pair for S3 authentication.

Spec:
- `providerRef` (object, required): `name` (and optional `namespace`)
- `displayName` (string, optional): Human-readable identifier
- `policy` (object, optional): Inline policy document (for IAM-like providers)
- `tags` (map[string]string, optional): Key-value tags
- `rotate` (object, optional):
  - `enabled` (bool, default: false)
  - `intervalDays` (int, default: 90): Days between rotations
  - `previousKeysRetentionDays` (int, default: 7): Keep old keys for N days

Status:
- `observedGeneration` (int)
- `accessKeyId` (string): Access key ID (generated)
- `created` (bool): Whether key was created in provider
- `lastRotateTime` (timestamp)
- `nextRotateTime` (timestamp)
- `conditions`: `Ready`, `ProviderNotReady`, `CreationFailed`, `RotationFailed`

## Phase 3: Operator Architecture

### 3. Reconciliation Model

#### 3.1 Handler Registration
- Register `@kopf.on.create`, `@kopf.on.update`, `@kopf.on.resume`, `@kopf.on.delete` for all CRDs
- Idempotent handlers only
- Configure controller limits: bounded worker pool, rate-limited requeues, leader election

#### 3.2 Provider Abstraction Layer
Create a provider abstraction that supports multiple S3 providers:
- Common interface for all S3 operations
- Provider-specific implementations for Wasabi, AWS, MinIO, etc.
- Use `boto3` for AWS-compatible providers (Wasabi supports AWS SDK)
- Use `minio` Python SDK for MinIO-specific features

Operations:
- Bucket CRUD: create, read, update, delete
- Policy management: get, put, delete
- Access key management: create, list, delete, rotate
- Lifecycle management: create, read, update, delete rules
- CORS management: create, read, update, delete rules
- Tagging: get, put
- Versioning: get, enable, suspend

#### 3.3 Provider Reconciliation
- Validate spec: endpoint format, auth secret presence
- Test connectivity: attempt S3 `list_buckets` API call
- Status updates:
  - Conditions: `AuthValid`, `EndpointReachable`, `Ready`
  - Emit Events on transitions

#### 3.4 Bucket Reconciliation
- Ensure `Provider` exists and is Ready
- Compute desired bucket configuration
- Diff against observed state (exist, versioning, encryption, tags)
- Apply configuration changes:
  - Create bucket if not exists
  - Update versioning state
  - Update encryption settings
  - Update public access settings
  - Manage lifecycle rules
  - Manage CORS rules
  - Sync tags
- Status updates:
  - Conditions: `Ready`, `ProviderNotReady`, `CreationFailed`
  - Bucket metadata (ARN, name, etc.)

#### 3.5 BucketPolicy Reconciliation
- Ensure `Bucket` exists and is Ready
- Validate policy document syntax
- Diff against observed policy
- Apply policy changes
- Status updates:
  - Conditions: `Ready`, `BucketNotReady`, `PolicyInvalid`, `ApplyFailed`

#### 3.6 AccessKey Reconciliation
- Ensure `Provider` exists and is Ready
- Create access key in provider
- Store credentials in Kubernetes Secret
- Handle rotation:
  - Create new key pair
  - Update referenced Secrets
  - Mark old key for deletion
  - Clean up old keys after retention period
- Status updates:
  - Conditions: `Ready`, `ProviderNotReady`, `CreationFailed`, `RotationFailed`

#### 3.7 Secret Management
- Never log credentials
- Store access keys in Kubernetes Secrets
- Use owner references to ensure cleanup
- Support key rotation with seamless updates

#### 3.8 Error Handling and Retries
- Implement retry logic with exponential backoff
- Handle provider-specific errors gracefully
- Surface errors via Conditions and Events
- Requeue on transient failures

### 4. Security Considerations

#### 4.1 Secret Handling
- Redact secrets from logs
- Use Kubernetes Secrets for all credentials
- Support secret rotation
- Never store credentials in CRD status

#### 4.2 RBAC
- Minimal permissions by default
- Provide presets: `minimal`, `scoped`, `full`
- Separate ServiceAccount for operator
- Support Pod Security Standards

#### 4.3 Provider Security
- Support TLS verification
- Support custom CA certificates
- Enforce least-privilege bucket policies
- Default to restrictive public access settings

### 5. Deployment and Packaging

#### 5.1 Helm Chart
- Structure:
  - `crds/`: CRD definitions (no templating)
  - `templates/`: Deployment, RBAC, ServiceAccount, Service(+ServiceMonitor)
  - `values.yaml`: `operator.*`, `rbac.*`, `watch.*`, `metrics.*`
- Values:
  - Pin images by digest
  - Enable leader election
  - Configure watch scope
  - Toggle ServiceMonitor

#### 5.2 Container Image
- Base: `python:3.14-alpine`
- Dependencies: `kopf`, `kubernetes`, `boto3`, `pyyaml`
- Minimal runtime with no unnecessary tools

## Phase 4: Observability and Testing

### 6. Observability

#### 6.1 Logs
- Structured JSON logs
- Fields: `controller`, `resource`, `uid`, `provider`, `event`, `reason`
- Never log secrets

#### 6.2 Events
- Emit Kubernetes Events for:
  - `ValidateSucceeded`, `ValidateFailed`
  - `BucketCreated`, `BucketUpdated`, `BucketDeleted`
  - `PolicyApplied`, `PolicyFailed`
  - `AccessKeyCreated`, `AccessKeyRotated`

#### 6.3 Metrics
Prometheus metrics:
- `wasabi_s3_operator_reconcile_total{kind,result}`
- `wasabi_s3_operator_reconcile_duration_seconds{kind}`
- `wasabi_s3_operator_bucket_operations_total{operation,result}`
- `wasabi_s3_operator_provider_connectivity{provider}`

#### 6.4 Status Conditions
Conditions for all CRDs:
- `Ready`: Overall readiness
- Provider errors: `ProviderNotReady`, `AuthFailed`, `EndpointUnreachable`
- Bucket errors: `CreationFailed`, `PolicyConflict`
- Access key errors: `CreationFailed`, `RotationFailed`

### 7. Testing Strategy

#### 7.1 Unit Tests
- CRD validation
- Provider abstraction layer
- Policy document validation
- Bucket configuration computation
- Error handling

#### 7.2 Integration Tests
- Use LocalStack or MinIO for local S3 testing
- Test CRUD operations for all resources
- Test provider connectivity
- Test error scenarios
- Test secret rotation

#### 7.3 Concurrency Tests
- Multiple buckets managed simultaneously
- Concurrent policy updates
- Access key rotation during active use

## Phase 5: Provider-Specific Features

### 8. Provider Implementations

#### 8.1 Wasabi
- Full support for Wasabi S3 API
- Region handling
- Special configuration notes

#### 8.2 AWS S3
- Full support for AWS S3
- IAM integration for advanced policies
- CloudTrail integration

#### 8.3 MinIO
- Full support for MinIO
- User management integration
- Bucket replication

#### 8.4 Generic S3
- Support for any S3-compatible provider
- Configurable endpoint and authentication

## Rollout and Compatibility

- Start at `v1alpha1` with clear migration notes
- Helm CRDs in `crds/` for robust install/upgrade
- Backward compatibility: provide conversion docs

## MVP Scope (First Release)

1. CRDs with validation, defaults, status/conditions
2. Provider abstraction layer with Wasabi and AWS support
3. Bucket CRUD operations with versioning and encryption
4. BucketPolicy management
5. AccessKey creation and rotation
6. Helm chart with RBAC presets
7. Observability: Events, metrics, structured logs
8. Tests: unit + integration with LocalStack

Note: This plan intentionally avoids writing code and focuses on architecture to guide implementation.

