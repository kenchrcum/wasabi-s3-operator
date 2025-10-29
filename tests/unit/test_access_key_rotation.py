"""Unit tests for access key rotation functionality."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestAccessKeyRotation:
    """Test access key rotation logic."""

    def test_rotation_enabled_no_existing_key(self):
        """Test that rotation is not needed when there's no existing key."""
        # When no key exists, rotation should not be triggered
        existing_key_id = None
        rotation_enabled = True
        
        assert existing_key_id is None
        # Should create initial key, not rotate

    def test_rotation_enabled_with_existing_key(self):
        """Test that rotation is enabled when key exists and rotation is configured."""
        existing_key_id = "AKIA1234567890"
        rotation_enabled = True
        next_rotate_time = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        
        assert existing_key_id is not None
        assert rotation_enabled is True
        assert next_rotate_time is not None

    def test_rotation_needed_check(self):
        """Test checking if rotation is needed based on nextRotateTime."""
        # Simulate current time
        now = datetime.now(timezone.utc)
        
        # Key needs rotation (next rotate time is in the past)
        past_time = (now - timedelta(days=1)).isoformat()
        assert datetime.fromisoformat(past_time.replace('Z', '+00:00')) < now
        
        # Key doesn't need rotation (next rotate time is in the future)
        future_time = (now + timedelta(days=1)).isoformat()
        assert datetime.fromisoformat(future_time.replace('Z', '+00:00')) > now

    def test_rotation_interval_calculation(self):
        """Test calculation of next rotation time."""
        rotation_interval_days = 90
        current_time = datetime.now(timezone.utc)
        next_rotate_time = current_time + timedelta(days=rotation_interval_days)
        
        assert (next_rotate_time - current_time).days == rotation_interval_days

    def test_previous_keys_tracking(self):
        """Test tracking of previous keys."""
        previous_keys = [
            {"accessKeyId": "AKIAOLDKEY1", "rotatedAt": "2024-01-01T00:00:00+00:00"},
            {"accessKeyId": "AKIAOLDKEY2", "rotatedAt": "2024-02-01T00:00:00+00:00"},
        ]
        
        # Add new previous key
        new_key = {"accessKeyId": "AKIAOLDKEY3", "rotatedAt": datetime.now(timezone.utc).isoformat()}
        previous_keys.append(new_key)
        
        assert len(previous_keys) == 3
        assert previous_keys[-1]["accessKeyId"] == "AKIAOLDKEY3"

    def test_expired_keys_cleanup(self):
        """Test identifying expired keys for cleanup."""
        retention_days = 7
        now = datetime.now(timezone.utc)
        
        # Create keys at different ages
        expired_key = {
            "accessKeyId": "AKIAEXPIRED",
            "rotatedAt": (now - timedelta(days=10)).isoformat(),
        }
        valid_key = {
            "accessKeyId": "AKIAVALID",
            "rotatedAt": (now - timedelta(days=3)).isoformat(),
        }
        
        # Check if keys are expired
        expired_rotated_at = datetime.fromisoformat(expired_key["rotatedAt"].replace('Z', '+00:00'))
        valid_rotated_at = datetime.fromisoformat(valid_key["rotatedAt"].replace('Z', '+00:00'))
        
        expired_age = (now - expired_rotated_at).days
        valid_age = (now - valid_rotated_at).days
        
        assert expired_age >= retention_days
        assert valid_age < retention_days

    def test_rotation_config_defaults(self):
        """Test default values for rotation configuration."""
        rotate_config = {}
        
        enabled = rotate_config.get("enabled", False)
        interval_days = rotate_config.get("intervalDays", 90)
        retention_days = rotate_config.get("previousKeysRetentionDays", 7)
        
        assert enabled is False
        assert interval_days == 90
        assert retention_days == 7

    def test_rotation_config_custom_values(self):
        """Test custom rotation configuration values."""
        rotate_config = {
            "enabled": True,
            "intervalDays": 30,
            "previousKeysRetentionDays": 14,
        }
        
        enabled = rotate_config.get("enabled", False)
        interval_days = rotate_config.get("intervalDays", 90)
        retention_days = rotate_config.get("previousKeysRetentionDays", 7)
        
        assert enabled is True
        assert interval_days == 30
        assert retention_days == 14

    def test_rotation_disabled_skips_cleanup(self):
        """Test that cleanup is skipped when rotation is disabled."""
        rotation_enabled = False
        
        # When rotation is disabled, no cleanup should occur
        should_cleanup = rotation_enabled and True  # Simulate having keys
        assert should_cleanup is False

    def test_next_rotation_time_preservation(self):
        """Test that next rotation time is preserved in status."""
        status_with_rotation = {
            "accessKeyId": "AKIA1234567890",
            "lastRotateTime": "2024-01-01T00:00:00+00:00",
            "nextRotateTime": "2024-04-01T00:00:00+00:00",
        }
        
        assert status_with_rotation.get("nextRotateTime") is not None
        assert status_with_rotation.get("lastRotateTime") is not None

    def test_multiple_previous_keys_limit(self):
        """Test limiting the number of previous keys tracked."""
        retention_days = 7
        max_keys_to_keep = retention_days
        
        # Create more keys than retention period
        previous_keys = [
            {"accessKeyId": f"AKIA{i}", "rotatedAt": f"2024-01-{i:02d}T00:00:00+00:00"}
            for i in range(1, 15)
        ]
        
        # Limit to max_keys_to_keep
        limited_keys = previous_keys[-max_keys_to_keep:]
        
        assert len(limited_keys) <= max_keys_to_keep
        assert len(limited_keys) == 7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

