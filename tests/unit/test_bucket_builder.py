"""Unit tests for bucket builder."""

from __future__ import annotations

from wasabi_s3_operator.builders.bucket import create_bucket_config_from_spec


class TestBucketBuilder:
    """Test bucket configuration builder."""

    def test_basic_config(self) -> None:
        """Test creating basic bucket configuration."""
        spec = {
            "name": "test-bucket",
            "providerRef": {"name": "test-provider"},
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["region"] == "us-east-1"
        assert config["versioning_enabled"] is False
        assert config["encryption_enabled"] is False
        assert config["tags"] is None
        assert config["lifecycle_rules"] == []
        assert config["cors_rules"] == []

    def test_versioning_config(self) -> None:
        """Test versioning configuration."""
        spec = {
            "name": "test-bucket",
            "versioning": {"enabled": True, "mfaDelete": True},
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["versioning_enabled"] is True
        assert config["mfa_delete"] is True

    def test_encryption_config(self) -> None:
        """Test encryption configuration."""
        spec = {
            "name": "test-bucket",
            "encryption": {
                "enabled": True,
                "algorithm": "aws:kms",
                "kmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012",
            },
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["encryption_enabled"] is True
        assert config["encryption_algorithm"] == "aws:kms"
        assert config["kms_key_id"] == "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"

    def test_tags_config(self) -> None:
        """Test tags configuration."""
        spec = {
            "name": "test-bucket",
            "tagging": {
                "tags": {
                    "Environment": "production",
                    "Owner": "platform-team",
                },
            },
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["tags"] == {"Environment": "production", "Owner": "platform-team"}

    def test_lifecycle_config(self) -> None:
        """Test lifecycle configuration."""
        spec = {
            "name": "test-bucket",
            "lifecycle": {
                "rules": [
                    {
                        "id": "delete-old-logs",
                        "status": "Enabled",
                        "prefix": "logs/",
                        "expiration": {"days": 30},
                    },
                    {
                        "id": "transition-to-glacier",
                        "status": "Enabled",
                        "transitions": [
                            {"days": 90, "storageClass": "GLACIER"},
                        ],
                    },
                ],
            },
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert len(config["lifecycle_rules"]) == 2
        assert config["lifecycle_rules"][0]["id"] == "delete-old-logs"
        assert config["lifecycle_rules"][1]["id"] == "transition-to-glacier"

    def test_cors_config(self) -> None:
        """Test CORS configuration."""
        spec = {
            "name": "test-bucket",
            "cors": {
                "rules": [
                    {
                        "allowedOrigins": ["https://example.com"],
                        "allowedMethods": ["GET", "POST"],
                        "allowedHeaders": ["*"],
                        "maxAgeSeconds": 3600,
                    },
                ],
            },
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert len(config["cors_rules"]) == 1
        assert config["cors_rules"][0]["allowedOrigins"] == ["https://example.com"]
        assert config["cors_rules"][0]["allowedMethods"] == ["GET", "POST"]

    def test_region_override(self) -> None:
        """Test region override."""
        spec = {
            "name": "test-bucket",
            "region": "us-west-2",
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["region"] == "us-west-2"

    def test_empty_lifecycle_and_cors(self) -> None:
        """Test empty lifecycle and CORS configurations."""
        spec = {
            "name": "test-bucket",
            "lifecycle": {},
            "cors": {},
        }
        config = create_bucket_config_from_spec(spec, "us-east-1")

        assert config["lifecycle_rules"] == []
        assert config["cors_rules"] == []

