"""Base S3 provider interface."""

from __future__ import annotations

from typing import Any, Protocol


class S3Provider(Protocol):
    """Protocol defining S3 provider operations."""

    def list_buckets(self) -> list[str]:
        """List all buckets in the provider."""
        ...

    def create_bucket(self, name: str, config: dict[str, Any]) -> None:
        """Create a bucket with the given configuration."""
        ...

    def delete_bucket(self, name: str) -> None:
        """Delete a bucket."""
        ...

    def bucket_exists(self, name: str) -> bool:
        """Check if a bucket exists."""
        ...

    def get_bucket_versioning(self, name: str) -> dict[str, bool]:
        """Get bucket versioning configuration."""
        ...

    def set_bucket_versioning(self, name: str, enabled: bool, mfa_delete: bool = False) -> None:
        """Set bucket versioning configuration."""
        ...

    def get_bucket_encryption(self, name: str) -> dict[str, str | None]:
        """Get bucket encryption configuration."""
        ...

    def set_bucket_encryption(self, name: str, algorithm: str, kms_key_id: str | None = None) -> None:
        """Set bucket encryption configuration."""
        ...

    def set_bucket_policy(self, name: str, policy: dict[str, Any]) -> None:
        """Set bucket policy."""
        ...

    def get_bucket_policy(self, name: str) -> dict[str, Any]:
        """Get bucket policy."""
        ...

    def delete_bucket_policy(self, name: str) -> None:
        """Delete bucket policy."""
        ...

    def set_bucket_tags(self, name: str, tags: dict[str, str]) -> None:
        """Set bucket tags."""
        ...

    def test_connectivity(self) -> bool:
        """Test connectivity to the provider."""
        ...

