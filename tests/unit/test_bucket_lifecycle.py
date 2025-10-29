"""Unit tests for bucket lifecycle management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from wasabi_s3_operator.services.aws.client import AWSProvider


class TestBucketLifecycle:
    """Test bucket lifecycle management."""

    @pytest.fixture
    def provider(self) -> AWSProvider:
        """Create a test provider."""
        return AWSProvider(
            endpoint="https://s3.wasabisys.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
        )

    def test_get_bucket_lifecycle_exists(self, provider: AWSProvider) -> None:
        """Test getting lifecycle configuration when it exists."""
        mock_response = {
            "Rules": [
                {
                    "ID": "test-rule",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "logs/"},
                    "Expiration": {"Days": 30},
                }
            ]
        }
        provider.client = MagicMock()
        provider.client.get_bucket_lifecycle_configuration.return_value = mock_response

        result = provider.get_bucket_lifecycle("test-bucket")
        assert result == mock_response
        provider.client.get_bucket_lifecycle_configuration.assert_called_once_with(Bucket="test-bucket")

    def test_get_bucket_lifecycle_not_exists(self, provider: AWSProvider) -> None:
        """Test getting lifecycle configuration when it doesn't exist."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchLifecycleConfiguration"}}
        provider.client.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, "GetBucketLifecycleConfiguration"
        )

        result = provider.get_bucket_lifecycle("test-bucket")
        assert result is None

    def test_get_bucket_lifecycle_error(self, provider: AWSProvider) -> None:
        """Test error handling when getting lifecycle fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied"}}
        provider.client.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, "GetBucketLifecycleConfiguration"
        )

        with pytest.raises(ClientError):
            provider.get_bucket_lifecycle("test-bucket")

    def test_set_bucket_lifecycle_simple(self, provider: AWSProvider) -> None:
        """Test setting simple lifecycle configuration."""
        provider.client = MagicMock()
        rules = [
            {
                "id": "test-rule",
                "status": "Enabled",
                "prefix": "logs/",
                "expiration": {"days": 30},
            }
        ]

        provider.set_bucket_lifecycle("test-bucket", rules)

        provider.client.put_bucket_lifecycle_configuration.assert_called_once()
        call_args = provider.client.put_bucket_lifecycle_configuration.call_args
        assert call_args[1]["Bucket"] == "test-bucket"
        lifecycle_config = call_args[1]["LifecycleConfiguration"]
        assert len(lifecycle_config["Rules"]) == 1
        assert lifecycle_config["Rules"][0]["ID"] == "test-rule"
        assert lifecycle_config["Rules"][0]["Status"] == "Enabled"
        assert lifecycle_config["Rules"][0]["Filter"]["Prefix"] == "logs/"
        assert lifecycle_config["Rules"][0]["Expiration"]["Days"] == 30

    def test_set_bucket_lifecycle_with_transitions(self, provider: AWSProvider) -> None:
        """Test setting lifecycle configuration with transitions."""
        provider.client = MagicMock()
        rules = [
            {
                "id": "test-rule",
                "status": "Enabled",
                "transitions": [
                    {"days": 30, "storageClass": "STANDARD_IA"},
                    {"days": 90, "storageClass": "GLACIER"},
                ],
            }
        ]

        provider.set_bucket_lifecycle("test-bucket", rules)

        call_args = provider.client.put_bucket_lifecycle_configuration.call_args
        lifecycle_config = call_args[1]["LifecycleConfiguration"]
        transitions = lifecycle_config["Rules"][0]["Transitions"]
        assert len(transitions) == 2
        assert transitions[0]["Days"] == 30
        assert transitions[0]["StorageClass"] == "STANDARD_IA"
        assert transitions[1]["Days"] == 90
        assert transitions[1]["StorageClass"] == "GLACIER"

    def test_set_bucket_lifecycle_with_date_expiration(self, provider: AWSProvider) -> None:
        """Test setting lifecycle configuration with date-based expiration."""
        provider.client = MagicMock()
        rules = [
            {
                "id": "test-rule",
                "status": "Enabled",
                "expiration": {"date": "2024-12-31"},
            }
        ]

        provider.set_bucket_lifecycle("test-bucket", rules)

        call_args = provider.client.put_bucket_lifecycle_configuration.call_args
        lifecycle_config = call_args[1]["LifecycleConfiguration"]
        assert lifecycle_config["Rules"][0]["Expiration"]["Date"] == "2024-12-31"

    def test_set_bucket_lifecycle_error(self, provider: AWSProvider) -> None:
        """Test error handling when setting lifecycle fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "InvalidRequest"}}
        provider.client.put_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, "PutBucketLifecycleConfiguration"
        )

        rules = [{"id": "test-rule", "status": "Enabled"}]
        with pytest.raises(ClientError):
            provider.set_bucket_lifecycle("test-bucket", rules)

    def test_delete_bucket_lifecycle_exists(self, provider: AWSProvider) -> None:
        """Test deleting lifecycle configuration when it exists."""
        provider.client = MagicMock()
        provider.delete_bucket_lifecycle("test-bucket")
        provider.client.delete_bucket_lifecycle.assert_called_once_with(Bucket="test-bucket")

    def test_delete_bucket_lifecycle_not_exists(self, provider: AWSProvider) -> None:
        """Test deleting lifecycle configuration when it doesn't exist."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchLifecycleConfiguration"}}
        provider.client.delete_bucket_lifecycle.side_effect = ClientError(
            error_response, "DeleteBucketLifecycle"
        )

        # Should not raise exception
        provider.delete_bucket_lifecycle("test-bucket")

    def test_delete_bucket_lifecycle_error(self, provider: AWSProvider) -> None:
        """Test error handling when deleting lifecycle fails."""
        provider.client = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied"}}
        provider.client.delete_bucket_lifecycle.side_effect = ClientError(
            error_response, "DeleteBucketLifecycle"
        )

        with pytest.raises(ClientError):
            provider.delete_bucket_lifecycle("test-bucket")

