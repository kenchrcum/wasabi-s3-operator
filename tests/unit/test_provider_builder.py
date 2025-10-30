"""Tests for provider builder."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from wasabi_s3_operator.builders.provider import create_provider_from_spec
from wasabi_s3_operator.services.aws.client import AWSProvider


class TestCreateProviderFromSpec:
    """Test cases for create_provider_from_spec function."""

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_success(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test successfully creating provider."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        # Mock secret values
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",  # access key
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # secret key
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {
                    "name": "wasabi-creds",
                    "key": "access-key",
                },
                "secretKeySecretRef": {
                    "name": "wasabi-creds",
                    "key": "secret-key",
                },
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        assert result == mock_provider_instance
        mock_aws_provider.assert_called_once_with(
            endpoint="s3.wasabisys.com",
            region="us-west-1",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            session_token=None,
            path_style=True,
            insecure_skip_verify=False,
            iam_endpoint=None,
            iam_region=None,
        )

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_with_session_token(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with session token."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "AQoDYXdzEPT//////////wEXAMPLEtc764",  # session token
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
                "sessionTokenSecretRef": {"name": "creds", "key": "session-token"},
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        assert result == mock_provider_instance
        # Verify session token was passed
        call_args = mock_aws_provider.call_args[1]
        assert call_args["session_token"] == "AQoDYXdzEPT//////////wEXAMPLEtc764"

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_with_tls_config(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with TLS configuration."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.example.com",
            "region": "us-east-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
            "tls": {
                "insecureSkipVerify": True,
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        call_args = mock_aws_provider.call_args[1]
        assert call_args["insecure_skip_verify"] is True

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_with_path_style(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with path style configuration."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.example.com",
            "region": "us-east-1",
            "pathStyle": False,
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        call_args = mock_aws_provider.call_args[1]
        assert call_args["path_style"] is False

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_with_iam_endpoint(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with IAM endpoint."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "iamEndpoint": "iam.wasabisys.com",
            "iamRegion": "us-east-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        call_args = mock_aws_provider.call_args[1]
        assert call_args["iam_endpoint"] == "iam.wasabisys.com"
        assert call_args["iam_region"] == "us-east-1"

    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_missing_access_key_ref(
        self, mock_load_config, mock_core_api
    ):
        """Test error when accessKeySecretRef is missing."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        with pytest.raises(ValueError, match="accessKeySecretRef and secretKeySecretRef are required"):
            create_provider_from_spec(spec, meta)

    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_missing_secret_key_ref(
        self, mock_load_config, mock_core_api
    ):
        """Test error when secretKeySecretRef is missing."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
            },
        }
        meta = {"namespace": "default"}
        
        with pytest.raises(ValueError, match="accessKeySecretRef and secretKeySecretRef are required"):
            create_provider_from_spec(spec, meta)

    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_missing_endpoint(
        self, mock_load_config, mock_get_secret, mock_core_api
    ):
        """Test error when endpoint is missing."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        with pytest.raises(ValueError, match="endpoint and region are required"):
            create_provider_from_spec(spec, meta)

    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_missing_region(
        self, mock_load_config, mock_get_secret, mock_core_api
    ):
        """Test error when region is missing."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        with pytest.raises(ValueError, match="endpoint and region are required"):
            create_provider_from_spec(spec, meta)

    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_unsupported_type(
        self, mock_load_config, mock_get_secret, mock_core_api
    ):
        """Test error with unsupported provider type."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "type": "unsupported-provider",
            "endpoint": "s3.example.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        with pytest.raises(ValueError, match="Unsupported provider type"):
            create_provider_from_spec(spec, meta)

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_kube_config")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_fallback_kubeconfig(
        self, mock_load_incluster, mock_load_kube, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with kubeconfig fallback."""
        from kubernetes.config import ConfigException
        
        mock_load_incluster.side_effect = ConfigException("Not in cluster")
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        assert result == mock_provider_instance
        mock_load_incluster.assert_called_once()
        mock_load_kube.assert_called_once()

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_default_namespace(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with default namespace."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds", "key": "access-key"},
                "secretKeySecretRef": {"name": "creds", "key": "secret-key"},
            },
        }
        meta = {}  # No namespace specified
        
        result = create_provider_from_spec(spec, meta)
        
        assert result == mock_provider_instance
        # Verify get_secret_value was called with "default" namespace
        assert mock_get_secret.call_args_list[0][0][1] == "default"

    @patch("wasabi_s3_operator.builders.provider.AWSProvider")
    @patch("wasabi_s3_operator.builders.provider.client.CoreV1Api")
    @patch("wasabi_s3_operator.builders.provider.get_secret_value")
    @patch("wasabi_s3_operator.builders.provider.config.load_incluster_config")
    def test_create_provider_default_keys(
        self, mock_load_config, mock_get_secret, mock_core_api, mock_aws_provider
    ):
        """Test creating provider with default secret keys."""
        mock_api = Mock()
        mock_core_api.return_value = mock_api
        
        mock_provider_instance = Mock()
        mock_aws_provider.return_value = mock_provider_instance
        
        mock_get_secret.side_effect = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        spec = {
            "endpoint": "s3.wasabisys.com",
            "region": "us-west-1",
            "auth": {
                "accessKeySecretRef": {"name": "creds"},  # No key specified
                "secretKeySecretRef": {"name": "creds"},  # No key specified
            },
        }
        meta = {"namespace": "default"}
        
        result = create_provider_from_spec(spec, meta)
        
        assert result == mock_provider_instance
        # Verify default keys were used
        assert mock_get_secret.call_args_list[0][0][3] == "access-key"
        assert mock_get_secret.call_args_list[1][0][3] == "secret-key"

