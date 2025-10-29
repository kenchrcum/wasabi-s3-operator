# Wasabi S3 Operator Documentation

Complete documentation for the Wasabi S3 Operator.

## Quick Start

- **[Getting Started](../README.md)** - Main project documentation with overview and quick start
- **[Quick Start Guide](../QUICKSTART.md)** - Get the operator running in minutes
- **[Deployment Guide](../DEPLOYMENT.md)** - Detailed deployment instructions

## Feature Documentation

### Core Features
- **[Access Key Rotation](./ACCESS_KEY_ROTATION.md)** - Automatic access key rotation guide
- **[IAM Policy Management](./IAM_POLICY.md)** - Reusable IAM policies for multiple users

### Architecture & Development
- **[Code Organization](./CODE_ORGANIZATION.md)** - Code structure, handler organization, and observability
- **[Versioning Strategy](./VERSIONING_STRATEGY.md)** - CRD versioning approach and migration planning

### Monitoring & Observability
- **[Grafana Dashboard](./grafana-dashboard-readme.md)** - Monitoring dashboard setup and configuration

## Examples

See the [examples directory](../examples/) for complete example manifests:
- [Examples README](../examples/README.md) - Overview of all examples
- [Simple Workflow](../examples/SIMPLE_WORKFLOW.md) - Auto-managed bucket workflow
- [User Workflow](../examples/USER_WORKFLOW.md) - Manual user management workflow

## Architecture Documentation

Located in the [`architecture/`](../architecture/) directory:

- **[Development Status](../architecture/STATUS.md)** - Current implementation status and roadmap
- **[CRD Specifications](../architecture/crd-specifications.md)** - Detailed CRD schemas and specifications
- **[Development Plan](../architecture/development-plan.md)** - Original architectural plan (historical reference)

## Documentation Structure

```
.
├── README.md                      # Main project documentation
├── QUICKSTART.md                  # Quick start guide
├── DEPLOYMENT.md                  # Deployment guide
├── docs/
│   ├── README.md                 # This file - documentation index
│   ├── ACCESS_KEY_ROTATION.md   # Access key rotation guide
│   ├── IAM_POLICY.md            # IAM policy management
│   ├── CODE_ORGANIZATION.md     # Code structure documentation
│   ├── VERSIONING_STRATEGY.md   # CRD versioning approach
│   └── grafana-dashboard-readme.md  # Monitoring dashboard
├── examples/
│   ├── README.md                # Examples overview
│   ├── SIMPLE_WORKFLOW.md       # Simple workflow guide
│   └── USER_WORKFLOW.md         # User workflow guide
└── architecture/
    ├── STATUS.md                 # Development status
    ├── crd-specifications.md    # CRD specifications
    └── development-plan.md      # Architectural plan (historical)
```

## Need Help?

- Check the [troubleshooting sections](../README.md#troubleshooting) in the main README
- Review [example manifests](../examples/) for common patterns
- Check [CRD specifications](../architecture/crd-specifications.md) for field details

