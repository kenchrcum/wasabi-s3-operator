"""Unit tests for Provider CRD."""

from __future__ import annotations

import pytest

from wasabi_s3_operator.services.aws.client import AWSProvider


class TestAWSProvider:
    """Test AWSProvider implementation."""

    def test_provider_initialization(self) -> None:
        """Test provider initialization."""
        provider = AWSProvider(
            endpoint="https://s3.wasabisys.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
        )

        assert provider.endpoint == "https://s3.wasabisys.com"
        assert provider.region == "us-east-1"
        assert provider.path_style is True

    def test_provider_with_session_token(self) -> None:
        """Test provider initialization with session token."""
        provider = AWSProvider(
            endpoint="https://s3.amazonaws.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
            session_token="test-session-token",
        )

        assert provider.endpoint == "https://s3.amazonaws.com"

    def test_provider_path_style_configuration(self) -> None:
        """Test provider path style configuration."""
        provider = AWSProvider(
            endpoint="https://s3.amazonaws.com",
            region="us-east-1",
            access_key="test-access-key",
            secret_key="test-secret-key",
            path_style=False,
        )

        assert provider.path_style is False

