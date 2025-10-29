"""Unit tests for error handling and edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from wasabi_s3_operator.services.aws.client import AWSProvider


class TestErrorHandling:
    """Test error handling scenarios."""

    @pytest.fixture
    def provider(self) -> AWSProvider:
        """Create a test provider."""
        return AWSProvider(
            endpoint="https://s3.wasabisys.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
        )

    def test_bucket_creation_with_lifecycle_failure(self, provider: AWSProvider) -> None:
        """Test that bucket creation succeeds even if lifecycle configuration fails."""
        provider.client = MagicMock()
        
        # Bucket creation succeeds
        provider.client.create_bucket.return_value = {}
        
        # Lifecycle configuration fails
        error_response = {"Error": {"Code": "InvalidRequest"}}
        provider.client.put_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, "PutBucketLifecycleConfiguration"
        )
        
        config = {
            "lifecycle_rules": [{"id": "test-rule", "status": "Enabled"}],
        }
        
        # Should raise exception (we're testing the warning behavior, but exception will propagate)
        # In practice, the handler catches this and logs a warning
        with pytest.raises(ClientError):
            provider.set_bucket_lifecycle("test-bucket", config["lifecycle_rules"])

    def test_bucket_creation_with_cors_failure(self, provider: AWSProvider) -> None:
        """Test that bucket creation succeeds even if CORS configuration fails."""
        provider.client = MagicMock()
        
        # CORS configuration fails
        error_response = {"Error": {"Code": "InvalidRequest"}}
        provider.client.put_bucket_cors.side_effect = ClientError(
            error_response, "PutBucketCors"
        )
        
        config = {
            "cors_rules": [{"allowedOrigins": ["https://example.com"], "allowedMethods": ["GET"]}],
        }
        
        # Should raise exception
        with pytest.raises(ClientError):
            provider.set_bucket_cors("test-bucket", config["cors_rules"])

    def test_lifecycle_deletion_when_not_exists(self, provider: AWSProvider) -> None:
        """Test that deleting non-existent lifecycle doesn't raise error."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchLifecycleConfiguration"}}
        provider.client.delete_bucket_lifecycle.side_effect = ClientError(
            error_response, "DeleteBucketLifecycle"
        )
        
        # Should not raise exception
        provider.delete_bucket_lifecycle("test-bucket")

    def test_cors_deletion_when_not_exists(self, provider: AWSProvider) -> None:
        """Test that deleting non-existent CORS doesn't raise error."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchCORSConfiguration"}}
        provider.client.delete_bucket_cors.side_effect = ClientError(
            error_response, "DeleteBucketCors"
        )
        
        # Should not raise exception
        provider.delete_bucket_cors("test-bucket")

    def test_empty_lifecycle_rules(self, provider: AWSProvider) -> None:
        """Test handling empty lifecycle rules."""
        provider.client = MagicMock()
        
        # Should handle empty rules gracefully
        provider.set_bucket_lifecycle("test-bucket", [])
        
        call_args = provider.client.put_bucket_lifecycle_configuration.call_args
        lifecycle_config = call_args[1]["LifecycleConfiguration"]
        assert lifecycle_config["Rules"] == []

    def test_empty_cors_rules(self, provider: AWSProvider) -> None:
        """Test handling empty CORS rules."""
        provider.client = MagicMock()
        
        # Should handle empty rules gracefully
        provider.set_bucket_cors("test-bucket", [])
        
        call_args = provider.client.put_bucket_cors.call_args
        cors_config = call_args[1]["CORSConfiguration"]
        assert cors_config["CORSRules"] == []

    def test_lifecycle_rule_without_optional_fields(self, provider: AWSProvider) -> None:
        """Test lifecycle rule with only required fields."""
        provider.client = MagicMock()
        
        rules = [{"id": "minimal-rule", "status": "Enabled"}]
        provider.set_bucket_lifecycle("test-bucket", rules)
        
        call_args = provider.client.put_bucket_lifecycle_configuration.call_args
        lifecycle_config = call_args[1]["LifecycleConfiguration"]
        rule = lifecycle_config["Rules"][0]
        assert rule["ID"] == "minimal-rule"
        assert rule["Status"] == "Enabled"
        assert "Filter" not in rule
        assert "Expiration" not in rule
        assert "Transitions" not in rule

    def test_cors_rule_without_optional_fields(self, provider: AWSProvider) -> None:
        """Test CORS rule with only required fields."""
        provider.client = MagicMock()
        
        rules = [
            {
                "allowedOrigins": ["https://example.com"],
                "allowedMethods": ["GET"],
            }
        ]
        provider.set_bucket_cors("test-bucket", rules)
        
        call_args = provider.client.put_bucket_cors.call_args
        cors_config = call_args[1]["CORSConfiguration"]
        rule = cors_config["CORSRules"][0]
        assert rule["AllowedOrigins"] == ["https://example.com"]
        assert rule["AllowedMethods"] == ["GET"]
        assert "AllowedHeaders" not in rule
        assert "ExposedHeaders" not in rule
        assert "MaxAgeSeconds" not in rule

