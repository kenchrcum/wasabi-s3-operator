"""Unit tests for bucket CORS management."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from wasabi_s3_operator.services.aws.client import AWSProvider


class TestBucketCORS:
    """Test bucket CORS management."""

    @pytest.fixture
    def provider(self) -> AWSProvider:
        """Create a test provider."""
        return AWSProvider(
            endpoint="https://s3.wasabisys.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
        )

    def test_get_bucket_cors_exists(self, provider: AWSProvider) -> None:
        """Test getting CORS configuration when it exists."""
        mock_response = {
            "CORSRules": [
                {
                    "AllowedOrigins": ["https://example.com"],
                    "AllowedMethods": ["GET", "POST"],
                    "AllowedHeaders": ["*"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        }
        provider.client = MagicMock()
        provider.client.get_bucket_cors.return_value = mock_response

        result = provider.get_bucket_cors("test-bucket")
        assert result == mock_response
        provider.client.get_bucket_cors.assert_called_once_with(Bucket="test-bucket")

    def test_get_bucket_cors_not_exists(self, provider: AWSProvider) -> None:
        """Test getting CORS configuration when it doesn't exist."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchCORSConfiguration"}}
        provider.client.get_bucket_cors.side_effect = ClientError(error_response, "GetBucketCors")

        result = provider.get_bucket_cors("test-bucket")
        assert result is None

    def test_get_bucket_cors_error(self, provider: AWSProvider) -> None:
        """Test error handling when getting CORS fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied"}}
        provider.client.get_bucket_cors.side_effect = ClientError(error_response, "GetBucketCors")

        with pytest.raises(ClientError):
            provider.get_bucket_cors("test-bucket")

    def test_set_bucket_cors_basic(self, provider: AWSProvider) -> None:
        """Test setting basic CORS configuration."""
        provider.client = MagicMock()
        rules = [
            {
                "allowedOrigins": ["https://example.com"],
                "allowedMethods": ["GET", "POST"],
            }
        ]

        provider.set_bucket_cors("test-bucket", rules)

        provider.client.put_bucket_cors.assert_called_once()
        call_args = provider.client.put_bucket_cors.call_args
        assert call_args[1]["Bucket"] == "test-bucket"
        cors_config = call_args[1]["CORSConfiguration"]
        assert len(cors_config["CORSRules"]) == 1
        assert cors_config["CORSRules"][0]["AllowedOrigins"] == ["https://example.com"]
        assert cors_config["CORSRules"][0]["AllowedMethods"] == ["GET", "POST"]

    def test_set_bucket_cors_full(self, provider: AWSProvider) -> None:
        """Test setting full CORS configuration with all fields."""
        provider.client = MagicMock()
        rules = [
            {
                "allowedOrigins": ["https://example.com", "https://app.example.com"],
                "allowedMethods": ["GET", "POST", "PUT", "DELETE"],
                "allowedHeaders": ["Content-Type", "Authorization"],
                "exposedHeaders": ["ETag", "x-amz-server-side-encryption"],
                "maxAgeSeconds": 86400,
            }
        ]

        provider.set_bucket_cors("test-bucket", rules)

        call_args = provider.client.put_bucket_cors.call_args
        cors_config = call_args[1]["CORSConfiguration"]
        rule = cors_config["CORSRules"][0]
        assert rule["AllowedOrigins"] == ["https://example.com", "https://app.example.com"]
        assert rule["AllowedMethods"] == ["GET", "POST", "PUT", "DELETE"]
        assert rule["AllowedHeaders"] == ["Content-Type", "Authorization"]
        assert rule["ExposedHeaders"] == ["ETag", "x-amz-server-side-encryption"]
        assert rule["MaxAgeSeconds"] == 86400

    def test_set_bucket_cors_multiple_rules(self, provider: AWSProvider) -> None:
        """Test setting CORS configuration with multiple rules."""
        provider.client = MagicMock()
        rules = [
            {
                "allowedOrigins": ["https://example.com"],
                "allowedMethods": ["GET"],
            },
            {
                "allowedOrigins": ["https://app.example.com"],
                "allowedMethods": ["POST", "PUT"],
            },
        ]

        provider.set_bucket_cors("test-bucket", rules)

        call_args = provider.client.put_bucket_cors.call_args
        cors_config = call_args[1]["CORSConfiguration"]
        assert len(cors_config["CORSRules"]) == 2

    def test_set_bucket_cors_error(self, provider: AWSProvider) -> None:
        """Test error handling when setting CORS fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "InvalidRequest"}}
        provider.client.put_bucket_cors.side_effect = ClientError(error_response, "PutBucketCors")

        rules = [{"allowedOrigins": ["https://example.com"], "allowedMethods": ["GET"]}]
        with pytest.raises(ClientError):
            provider.set_bucket_cors("test-bucket", rules)

    def test_delete_bucket_cors_exists(self, provider: AWSProvider) -> None:
        """Test deleting CORS configuration when it exists."""
        provider.client = MagicMock()
        provider.delete_bucket_cors("test-bucket")
        provider.client.delete_bucket_cors.assert_called_once_with(Bucket="test-bucket")

    def test_delete_bucket_cors_not_exists(self, provider: AWSProvider) -> None:
        """Test deleting CORS configuration when it doesn't exist."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchCORSConfiguration"}}
        provider.client.delete_bucket_cors.side_effect = ClientError(error_response, "DeleteBucketCors")

        # Should not raise exception
        provider.delete_bucket_cors("test-bucket")

    def test_delete_bucket_cors_error(self, provider: AWSProvider) -> None:
        """Test error handling when deleting CORS fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied"}}
        provider.client.delete_bucket_cors.side_effect = ClientError(error_response, "DeleteBucketCors")

        with pytest.raises(ClientError):
            provider.delete_bucket_cors("test-bucket")

