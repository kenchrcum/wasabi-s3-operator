# CRD Versioning Strategy

## Overview

This document outlines the versioning strategy for the Wasabi S3 Operator CRDs. As the operator evolves, breaking changes to CRD schemas require careful versioning and migration planning.

## Version Scheme

### Current Version: v1alpha1

- **Alpha** (`v1alpha1`): Initial implementation, may have breaking changes
- **Beta** (`v1beta1`): Stable API, minor breaking changes possible
- **Stable** (`v1`): Production-ready, backward compatible

## Version Lifecycle

### Alpha (v1alpha1)

**Characteristics:**
- Initial development phase
- Breaking changes allowed without deprecation
- No guarantee of backward compatibility
- Subject to frequent changes

**Migration:**
- Manual migration required between alpha versions
- No automatic conversion webhooks
- Users must recreate resources or manually convert

### Beta (v1beta1)

**Characteristics:**
- API is considered stable for non-breaking changes
- Breaking changes require deprecation period
- Conversion webhooks for forward compatibility
- Support for multiple concurrent versions

**Migration:**
- Automatic conversion from v1alpha1 to v1beta1 via webhook
- Backward compatibility with v1alpha1 resources
- Gradual migration path

### Stable (v1)

**Characteristics:**
- Production-ready, mature API
- Strict backward compatibility requirements
- Long-term support commitment
- Conversion webhooks for all previous versions

**Migration:**
- Automatic conversion from v1alpha1/v1beta1 to v1
- Seamless upgrade path
- Deprecated fields marked but supported

## Version Upgrade Process

### Step 1: Planning

1. **Identify Breaking Changes**
   - Schema changes (field removals, type changes)
   - Behavioral changes that affect existing resources
   - Validation rule changes

2. **Document Changes**
   - Create migration guide for affected resources
   - Document any manual steps required
   - Provide example manifests for new version

3. **Create Conversion Webhooks** (Beta/Stable only)
   - Implement conversion logic for supported versions
   - Test conversion paths thoroughly
   - Handle edge cases and default values

### Step 2: Implementation

1. **Add New CRD Version**
   ```yaml
   apiVersion: apiextensions.k8s.io/v1
   kind: CustomResourceDefinition
   metadata:
     name: providers.s3.cloud37.dev
   spec:
     versions:
       - name: v1alpha1
         served: true
         storage: false  # Not default storage anymore
       - name: v1beta1
         served: true
         storage: true   # New default storage version
   ```

2. **Implement Conversion Webhook** (if applicable)
   - Create conversion webhook server
   - Implement conversion functions
   - Register webhook with Kubernetes

3. **Update Operator Handlers**
   - Support multiple versions in handlers
   - Add version-specific logic if needed
   - Maintain backward compatibility

### Step 3: Migration

1. **Deprecation Period** (Beta/Stable)
   - Announce deprecation with timeline
   - Support both versions during transition
   - Provide migration tools/documentation

2. **Automatic Conversion** (Beta/Stable)
   - Resources automatically converted to new version
   - No user action required for read operations
   - Write operations use new version format

3. **Manual Migration** (Alpha)
   - Users recreate resources with new version
   - Export existing resources
   - Import with updated manifests

## Example: v1alpha1 → v1beta1 Migration

### Scenario

Breaking change: `spec.endpoint` field renamed to `spec.apiEndpoint`

### Steps

1. **Add v1beta1 CRD** with new field name
2. **Implement Conversion Webhook**:
   ```python
   def convert_v1alpha1_to_v1beta1(obj):
       if 'endpoint' in obj['spec']:
           obj['spec']['apiEndpoint'] = obj['spec'].pop('endpoint')
       return obj
   ```
3. **Update Handlers** to support both fields during transition
4. **Deprecate v1alpha1** after migration period

## Best Practices

### For Operators

1. **Version Early**: Introduce new versions before breaking changes
2. **Support Multiple Versions**: During transition periods
3. **Automatic Conversion**: Use webhooks when possible
4. **Clear Documentation**: Provide migration guides
5. **Backward Compatibility**: Maintain as long as feasible

### For Users

1. **Stay Current**: Migrate to stable versions when available
2. **Test Migrations**: Test in non-production first
3. **Backup Resources**: Export resources before upgrades
4. **Follow Migration Guides**: Follow documented procedures
5. **Report Issues**: Report conversion problems

## Version Support Matrix

| Operator Version | Supported CRD Versions | Default Version |
|----------------|----------------------|-----------------|
| 0.1.x          | v1alpha1             | v1alpha1        |
| 0.2.x          | v1alpha1, v1beta1    | v1beta1         |
| 1.0.x          | v1alpha1, v1beta1, v1| v1               |

## Future Considerations

### Conversion Webhooks

Implementation plan for v1beta1:
- Create webhook server deployment
- Implement conversion functions
- Add webhook configuration to Helm chart
- Test all conversion paths

### Migration Tools

Potential migration utilities:
- `kubectl` plugin for resource conversion
- Migration script for bulk operations
- Validation tool for version compatibility

## References

- [Kubernetes API Versioning](https://kubernetes.io/docs/reference/using-api/api-concepts/#versioning)
- [CRD Versioning](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/)
- [Webhook Conversion](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/#webhook-conversion)

