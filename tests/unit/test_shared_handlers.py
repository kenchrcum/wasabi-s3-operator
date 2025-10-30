"""Tests for shared handler utilities."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from kubernetes import client

from wasabi_s3_operator.handlers.shared import (
    get_k8s_client,
    get_provider_with_cache,
    get_user_with_cache,
)


class TestGetProviderWithCache:
    """Test cases for get_provider_with_cache function."""

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_provider_from_cache(self, mock_metrics, mock_get_cached):
        """Test getting provider from cache."""
        mock_api = Mock()
        cached_provider = {"metadata": {"name": "test-provider"}, "spec": {}}
        mock_get_cached.return_value = cached_provider

        result = get_provider_with_cache(mock_api, "test-provider", "default")

        assert result == cached_provider
        # API should not be called
        mock_api.get_namespaced_custom_object.assert_not_called()
        # Cache hit metric should be recorded
        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_provider", result="cache_hit"
        )

    @patch("wasabi_s3_operator.handlers.shared.set_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_provider_from_api(self, mock_metrics, mock_rate_limit, mock_get_cached, mock_set_cached):
        """Test getting provider from API when not in cache."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        provider_obj = {"metadata": {"name": "test-provider"}, "spec": {}}
        
        # Mock rate_limit_k8s to return a function that calls the API
        mock_api_method = Mock(return_value=provider_obj)
        mock_rate_limit.return_value = mock_api_method

        result = get_provider_with_cache(mock_api, "test-provider", "default")

        assert result == provider_obj
        # API should be called
        mock_api_method.assert_called_once()
        # Provider should be cached
        mock_set_cached.assert_called_once()
        # Success metric should be recorded
        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_provider", result="success"
        )

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.handle_rate_limit_error")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_provider_with_rate_limit(
        self, mock_metrics, mock_rate_limit, mock_handle_rate_limit, mock_get_cached
    ):
        """Test getting provider with rate limit error."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        rate_limit_error = Exception("Rate limit exceeded")
        
        # First call raises rate limit error, second call succeeds
        provider_obj = {"metadata": {"name": "test-provider"}, "spec": {}}
        mock_api_method = Mock(side_effect=[rate_limit_error, provider_obj])
        mock_rate_limit.return_value = mock_api_method
        
        # Mock handle_rate_limit_error to return True (handled)
        mock_handle_rate_limit.return_value = True

        result = get_provider_with_cache(mock_api, "test-provider", "default")

        assert result == provider_obj
        # Should be called twice (retry after rate limit)
        assert mock_api_method.call_count == 2

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.handle_rate_limit_error")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_provider_api_error(
        self, mock_metrics, mock_rate_limit, mock_handle_rate_limit, mock_get_cached
    ):
        """Test getting provider with API error."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        api_error = client.exceptions.ApiException(status=404, reason="Not Found")
        
        mock_api_method = Mock(side_effect=api_error)
        mock_rate_limit.return_value = mock_api_method
        mock_handle_rate_limit.return_value = False  # Not a rate limit error

        with pytest.raises(client.exceptions.ApiException):
            get_provider_with_cache(mock_api, "test-provider", "default")

        # Error metric should be recorded
        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_provider", result="error"
        )


class TestGetUserWithCache:
    """Test cases for get_user_with_cache function."""

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_user_from_cache(self, mock_metrics, mock_get_cached):
        """Test getting user from cache."""
        mock_api = Mock()
        cached_user = {"metadata": {"name": "test-user"}, "spec": {}}
        mock_get_cached.return_value = cached_user

        result = get_user_with_cache(mock_api, "test-user", "default")

        assert result == cached_user
        # API should not be called
        mock_api.get_namespaced_custom_object.assert_not_called()
        # Cache hit metric should be recorded
        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_user", result="cache_hit"
        )

    @patch("wasabi_s3_operator.handlers.shared.set_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_user_from_api(self, mock_metrics, mock_rate_limit, mock_get_cached, mock_set_cached):
        """Test getting user from API when not in cache."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        user_obj = {"metadata": {"name": "test-user"}, "spec": {}}
        
        mock_api_method = Mock(return_value=user_obj)
        mock_rate_limit.return_value = mock_api_method

        result = get_user_with_cache(mock_api, "test-user", "default")

        assert result == user_obj
        # API should be called
        mock_api_method.assert_called_once()
        # User should be cached
        mock_set_cached.assert_called_once()
        # Success metric should be recorded
        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_user", result="success"
        )

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.handle_rate_limit_error")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_user_with_retry(
        self, mock_metrics, mock_rate_limit, mock_handle_rate_limit, mock_get_cached
    ):
        """Test getting user with retry logic."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        rate_limit_error = Exception("Rate limit")
        
        user_obj = {"metadata": {"name": "test-user"}, "spec": {}}
        mock_api_method = Mock(side_effect=[rate_limit_error, user_obj])
        mock_rate_limit.return_value = mock_api_method
        mock_handle_rate_limit.return_value = True

        result = get_user_with_cache(mock_api, "test-user", "default")

        assert result == user_obj
        assert mock_api_method.call_count == 2

    @patch("wasabi_s3_operator.handlers.shared.get_cached_object")
    @patch("wasabi_s3_operator.handlers.shared.handle_rate_limit_error")
    @patch("wasabi_s3_operator.handlers.shared.rate_limit_k8s")
    @patch("wasabi_s3_operator.handlers.shared.metrics")
    def test_get_user_error(
        self, mock_metrics, mock_rate_limit, mock_handle_rate_limit, mock_get_cached
    ):
        """Test getting user with non-rate-limit error."""
        mock_api = Mock()
        mock_get_cached.return_value = None
        api_error = Exception("API error")
        
        mock_api_method = Mock(side_effect=api_error)
        mock_rate_limit.return_value = mock_api_method
        mock_handle_rate_limit.return_value = False

        with pytest.raises(Exception, match="API error"):
            get_user_with_cache(mock_api, "test-user", "default")

        mock_metrics.api_call_total.labels.assert_called_with(
            api_type="k8s", operation="get_user", result="error"
        )


class TestGetK8sClient:
    """Test cases for get_k8s_client function."""

    @patch("wasabi_s3_operator.handlers.shared.client.CustomObjectsApi")
    @patch("kubernetes.config.load_incluster_config")
    def test_get_k8s_client_incluster(self, mock_load_incluster, mock_api):
        """Test getting K8s client with incluster config."""
        mock_api_instance = Mock()
        mock_api.return_value = mock_api_instance

        result = get_k8s_client()

        assert result == mock_api_instance
        mock_load_incluster.assert_called_once()

    @patch("wasabi_s3_operator.handlers.shared.client.CustomObjectsApi")
    @patch("kubernetes.config.load_kube_config")
    @patch("kubernetes.config.load_incluster_config")
    def test_get_k8s_client_kubeconfig(self, mock_load_incluster, mock_load_kube, mock_api):
        """Test getting K8s client with kubeconfig fallback."""
        from kubernetes.config import ConfigException
        
        mock_load_incluster.side_effect = ConfigException("Not in cluster")
        mock_api_instance = Mock()
        mock_api.return_value = mock_api_instance

        result = get_k8s_client()

        assert result == mock_api_instance
        mock_load_incluster.assert_called_once()
        mock_load_kube.assert_called_once()


