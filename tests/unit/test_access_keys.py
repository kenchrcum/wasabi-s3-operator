"""Unit tests for access key utilities."""

from __future__ import annotations

from wasabi_s3_provider.utils.access_keys import (
    generate_access_key_id,
    generate_secret_access_key,
)


class TestAccessKeyGeneration:
    """Test access key generation utilities."""

    def test_generate_access_key_id(self) -> None:
        """Test access key ID generation."""
        key_id = generate_access_key_id()

        assert len(key_id) == 20
        assert key_id.isalnum()
        assert key_id.isupper() or key_id.isdigit()

    def test_generate_secret_access_key(self) -> None:
        """Test secret access key generation."""
        secret_key = generate_secret_access_key()

        assert len(secret_key) == 40
        # Should contain alphanumeric characters and +/
        assert all(c.isalnum() or c in "+/" for c in secret_key)

    def test_generate_multiple_keys_unique(self) -> None:
        """Test that multiple generated keys are unique."""
        key_ids = [generate_access_key_id() for _ in range(10)]
        secret_keys = [generate_secret_access_key() for _ in range(10)]

        # All IDs should be unique
        assert len(set(key_ids)) == 10
        # All secret keys should be unique
        assert len(set(secret_keys)) == 10

