# Wasabi S3 Operator

![Version](https://img.shields.io/badge/version-0.2.5-blue.svg)
[![License](https://img.shields.io/badge/license-Unlicense-lightgrey.svg)](LICENSE)
[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/wasabi-s3-operator)](https://artifacthub.io/packages/search?repo=wasabi-s3-operator)

A Kubernetes operator for managing Wasabi S3 storage using the [Kopf](https://kopf.readthedocs.io) framework. Built with security, observability, and operational simplicity as core principles.

## 🎯 Overview

The Wasabi S3 Operator brings declarative S3 bucket management directly into your Kubernetes workflows. It enables you to:

- **Manage Wasabi S3 buckets** through Kubernetes CRDs
- **Configure bucket policies** with IAM-style policy documents
- **Manage access keys** with automatic rotation support
- **Wasabi-focused** - Optimized specifically for Wasabi's S3-compatible API
- **Secure by default** with least-privilege RBAC, secret management, and security best practices
- **Observable** with Prometheus metrics, structured logging, and Kubernetes Events

### Key Features

✨ **Six Declarative CRDs**
- `Provider` — Define Wasabi S3 provider connections
- `Bucket` — Manage S3 buckets with versioning, encryption, lifecycle rules, CORS
- `BucketPolicy` — Apply IAM-style bucket policies
- `User` — Manage IAM users with inline or referenced policies
- `IAMPolicy` — Reusable IAM policies for multiple users
- `AccessKey` — Manage access keys with automatic rotation

🔐 **Security First**
- Never store credentials in CRD status
- Kubernetes Secrets for all credentials
- Automatic secret rotation support
- Least-privilege RBAC by default
- TLS verification enforced
- Support for MFA-protected operations

☁️ **Wasabi-Optimized**
- Native Wasabi support
- Optimized for Wasabi's S3-compatible API
- Full feature support for Wasabi-specific capabilities

📊 **Production Ready Observability**
- Prometheus metrics (reconciliation counters, durations, S3 operations)
- Kubernetes Events for lifecycle transitions
- Structured JSON logs with correlation IDs
- Status conditions following Kubernetes conventions

## 📋 Prerequisites

- Kubernetes 1.24+ cluster
- Helm 3.8+
- Optional: Prometheus for metrics collection

## 🚀 Quick Start

### Installation

Install the operator using Helm:

```bash
helm install wasabi-s3-operator ./helm/wasabi-s3-operator \
  --namespace wasabi-s3-operator-system \
  --create-namespace
```

### Basic Example

1. **Create a Provider** for your S3-compatible storage:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Provider
metadata:
  name: wasabi-us-east-1
spec:
  type: wasabi
  endpoint: https://s3.wasabisys.com
  region: us-east-1
  auth:
    accessKeySecretRef:
      name: wasabi-credentials
      key: access-key
    secretKeySecretRef:
      name: wasabi-credentials
      key: secret-key
```

2. **Create a Bucket**:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: Bucket
metadata:
  name: my-bucket
spec:
  providerRef:
    name: wasabi-us-east-1
  name: my-bucket-name
  versioning:
    enabled: true
  encryption:
    enabled: true
    algorithm: AES256
  publicAccess:
    blockPublicAcls: true
    blockPublicPolicy: true
```

3. **Apply a Bucket Policy**:

```yaml
apiVersion: s3.cloud37.dev/v1alpha1
kind: BucketPolicy
metadata:
  name: my-bucket-policy
spec:
  bucketRef:
    name: my-bucket
  policy:
    version: "2012-10-17"
    statement:
      - effect: Allow
        principal: "*"
        action: s3:GetObject
        resource: "arn:aws:s3:::my-bucket-name/*"
```

## 📚 Custom Resource Definitions

The operator manages six CRDs for complete Wasabi S3 infrastructure management:

### Provider

Represents a Wasabi S3 provider connection.

**Key Fields:**
- `spec.type` (required) — Provider type: `wasabi`
- `spec.endpoint` (required) — Wasabi S3 API endpoint URL (e.g., `https://s3.wasabisys.com`)
- `spec.iamEndpoint` (optional) — Wasabi IAM endpoint URL (required for user management, e.g., `https://iam.wasabisys.com`)
- `spec.region` (required) — Provider region
- `spec.auth` (required) — Authentication configuration via Kubernetes Secrets
- `spec.tls` (optional) — TLS configuration
- `spec.pathStyle` (default: `true`) — Use path-style addressing (required for Wasabi)

**Status Conditions:**
- `AuthValid` — Credentials validation status
- `EndpointReachable` — Provider endpoint connectivity
- `Ready` — Overall readiness

### Bucket

Represents an S3 bucket managed by the operator.

**Key Fields:**
- `spec.providerRef.name` (required) — Reference to Provider
- `spec.name` (required) — Bucket name (DNS-compliant)
- `spec.versioning` — Versioning configuration
- `spec.encryption` — Encryption at rest configuration
- `spec.publicAccess` — Public access block settings
- `spec.lifecycle` — Lifecycle rules for object management
- `spec.cors` — CORS configuration
- `spec.autoManage` — Automatic user, policy, and access key creation

**Status Conditions:**
- `Ready` — Bucket is ready and synchronized
- `ProviderNotReady` — Referenced Provider is not ready
- `CreationFailed` — Bucket creation failed

### BucketPolicy

Represents an IAM-style bucket policy document.

**Key Fields:**
- `spec.bucketRef.name` (required) — Reference to Bucket
- `spec.policy` (required) — IAM policy document (JSON)

**Status Conditions:**
- `Ready` — Policy is applied and synchronized
- `BucketNotReady` — Referenced Bucket is not ready
- `PolicyInvalid` — Policy document validation failed

### User

Represents an IAM user for Wasabi S3 access.

**Key Fields:**
- `spec.providerRef.name` (required) — Reference to Provider (must have `iamEndpoint` configured)
- `spec.name` (required) — IAM user name
- `spec.policy` (optional) — Inline IAM policy document
- `spec.policyRef` (optional) — Reference to IAMPolicy resource (mutually exclusive with `policy`)
- `spec.tags` (optional) — User tags

**Status Conditions:**
- `Ready` — User is ready and synchronized
- `ProviderNotReady` — Referenced Provider is not ready
- `CreationFailed` — User creation failed

### IAMPolicy

Represents a reusable IAM policy that can be attached to multiple users.

**Key Fields:**
- `spec.providerRef.name` (required) — Reference to Provider
- `spec.policy` (required) — IAM policy document (JSON)

**Status Conditions:**
- `Ready` — Policy is ready to be attached
- `ProviderNotReady` — Referenced Provider is not ready
- `CreationFailed` — Policy creation failed

### AccessKey

Represents an access key pair for S3 authentication.

**Key Fields:**
- `spec.providerRef.name` (required) — Reference to Provider
- `spec.userRef.name` (required) — Reference to User resource
- `spec.displayName` (optional) — Human-readable identifier
- `spec.rotate` — Automatic rotation configuration

**Status Conditions:**
- `Ready` — Access key is ready and synchronized
- `ProviderNotReady` — Referenced Provider is not ready
- `UserNotReady` — Referenced User is not ready
- `CreationFailed` — Access key creation failed
- `RotationFailed` — Access key rotation failed

## 🔒 Security

### Credential Management

- **Never stored in CRD status** — All credentials stored in Kubernetes Secrets
- **Secret rotation** — Automatic rotation support for access keys
- **Least-privilege RBAC** — Minimal permissions by default
- **TLS verification** — Enforced by default (can be disabled for development)

### RBAC Presets

Configure via Helm values:

- **`minimal`** (default) — Namespace-scoped permissions
- **`scoped`** — Extended permissions for specific resources
- **`full`** (opt-in) — Full cluster access (use with caution)

### Bucket Security

- **Default to private** — Buckets are private by default
- **Block public access** — Public access blocked by default
- **Encryption support** — AES256 and AWS KMS encryption support
- **MFA protection** — Support for MFA-protected delete operations

## 📊 Observability

### Metrics

The operator exposes Prometheus metrics on port `8080`:

- `wasabi_s3_operator_reconcile_total{kind,result}` — Reconciliation counts
- `wasabi_s3_operator_reconcile_duration_seconds{kind}` — Reconciliation latency histogram
- `wasabi_s3_operator_bucket_operations_total{operation,result}` — S3 operation counts
- `wasabi_s3_operator_provider_connectivity{provider}` — Provider connectivity status

### Events

The operator emits Kubernetes Events for:
- Provider connectivity changes
- Bucket creation/update/deletion
- Policy application failures
- Access key rotation events

### Logs

Structured JSON logs with fields:
- `controller`, `resource`, `uid`, `provider`, `event`, `reason`

**No secrets are logged.** The operator sanitizes all log output.

## 🛠️ Development

### Prerequisites

- Python 3.14+
- `uv` or `pip` for dependency management
- Pre-commit hooks configured

### Setup

```bash
# Clone repository
git clone <repository-url>
cd wasabi-s3-operator

# Install dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install

# Run tests
pytest tests/
```

### Project Structure

```
.
├── src/
│   └── wasabi_s3_operator/
│       ├── main.py              # Kopf handlers and reconciliation logic
│       ├── metrics.py           # Prometheus metrics definitions
│       ├── constants.py         # API group and label constants
│       ├── builders/             # Resource builders
│       ├── services/            # S3 service implementations
│       │   ├── s3/              # S3 operations
│       │   └── aws/              # AWS/Wasabi compatible operations
│       └── utils/               # Utility functions
├── helm/
│   └── wasabi-s3-operator/
│       ├── crds/                # CRD definitions (not templated)
│       ├── templates/           # Helm templates for operator deployment
│       └── values.yaml          # Default Helm values
├── tests/
│   ├── unit/                    # Unit tests
│   └── integration/             # Integration tests
├── examples/                    # Example CRs
├── architecture/               # Architecture documentation
└── docs/                        # Additional documentation
```

### Testing

```bash
# Unit tests
pytest tests/unit/

# Integration tests
pytest tests/integration/

# Run with coverage
pytest --cov=wasabi_s3_operator tests/
```

### Code Quality

The project uses:
- **`ruff`** — Fast Python linter
- **`ruff format`** / **`black`** — Code formatting
- **`mypy`** — Static type checking
- **`pytest`** — Testing framework

**Pre-commit checks:**

```bash
pre-commit run --all-files
```

## 📖 Documentation

### Architecture Documentation
- [Development Status](./architecture/STATUS.md) - Current development status and roadmap
- [Development Plan](./architecture/development-plan.md) - Original architectural plan (historical reference)
- [CRD Specifications](./architecture/crd-specifications.md) - Detailed CRD schemas and specifications

### Additional Documentation
- [Access Key Rotation](./docs/ACCESS_KEY_ROTATION.md) - Automatic key rotation guide
- [IAM Policy Management](./docs/IAM_POLICY.md) - Reusable IAM policies
- [Code Organization](./docs/CODE_ORGANIZATION.md) - Code structure and organization
- [Versioning Strategy](./docs/VERSIONING_STRATEGY.md) - CRD versioning approach
- [Grafana Dashboard](./docs/grafana-dashboard-readme.md) - Monitoring dashboard setup

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feat/my-feature`
3. **Follow the code style**: Run `pre-commit run --all-files`
4. **Add tests** for new functionality
5. **Update documentation** as needed
6. **Commit with Conventional Commits**: `feat:`, `fix:`, `docs:`, etc.
7. **Submit a Pull Request**

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## 📝 License

This project is licensed under the **Unlicense**.

## 🔗 Links

- **Repository**: `<repository-url>`
- **Issues**: `<repository-url>/issues`
- **Helm Chart**: `./helm/wasabi-s3-operator`

## 🙏 Acknowledgments

- Built with [Kopf](https://kopf.readthedocs.io) — Kubernetes Operator Pythonic Framework
- Uses [kubernetes-client/python](https://github.com/kubernetes-client/python)
- Uses [boto3](https://boto3.amazonaws.com) for AWS/S3 operations

---

**Status:** v1alpha1 — Wasabi S3 Operator

This operator is specifically designed for Wasabi S3 storage, providing declarative bucket management, policies, and access key management.

