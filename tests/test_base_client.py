"""Tests for the Wallapop base API client."""
from unittest.mock import patch, MagicMock

import pytest

from src.extractors.base_client import WallapopAPIClient


class ConcreteExtractor(WallapopAPIClient):
    """Minimal concrete implementation for testing the abstract base."""
    def extract(self, **kwargs):
        return {"test": True}


def _make_client(**kwargs):
    return ConcreteExtractor(
        bearer_token="test-bearer-123",
        user_agents=["TestAgent/1.0"],
        **kwargs,
    )


class TestBuildHeaders:

    def test_includes_bearer_token(self):
        client = _make_client()
        headers = client._build_headers()
        assert headers["Authorization"] == "Bearer test-bearer-123"

    def test_includes_user_agent(self):
        client = _make_client()
        headers = client._build_headers()
        assert headers["User-Agent"] == "TestAgent/1.0"

    def test_details_endpoint_type(self):
        client = _make_client()
        headers = client._build_headers(endpoint_type="details")
        assert "X-AppVersion" in headers
        assert "X-DeviceOS" in headers
        assert "X-Device-ID" not in headers

    def test_default_endpoint_type(self):
        client = _make_client()
        headers = client._build_headers(endpoint_type="default")
        assert "X-Device-ID" in headers
        assert "X-Platform" in headers
        assert headers["X-Platform"] == "android"

    def test_device_id_format(self):
        client = _make_client()
        device_id = client._generate_device_id()
        assert device_id.startswith("android-")
        assert len(device_id) == 24  # "android-" + 16 hex chars


class TestRequest:

    @patch("src.extractors.base_client.requests.get")
    def test_successful_request(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        client = _make_client()
        result = client._request("https://api.wallapop.com/test", {})

        assert result == {"data": "test"}

    @patch("src.extractors.base_client.requests.get")
    def test_401_triggers_refresh(self, mock_get):
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {"refreshed": True}

        mock_get.side_effect = [mock_response_401, mock_response_200]

        refresh_called = []
        def mock_refresh():
            refresh_called.append(True)
            return "new-bearer-456"

        client = _make_client(token_refresh_callback=mock_refresh)
        result = client._request("https://api.wallapop.com/test", {"Authorization": "Bearer old"})

        assert result == {"refreshed": True}
        assert len(refresh_called) == 1
        assert client.bearer == "new-bearer-456"

    @patch("src.extractors.base_client.requests.get")
    def test_401_without_refresh_returns_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        client = _make_client()
        result = client._request("https://api.wallapop.com/test", {})

        assert result is None
        assert "401" in client.last_error

    @patch("src.extractors.base_client.requests.get")
    @patch("src.extractors.base_client.time.sleep")
    def test_429_retries_with_backoff(self, mock_sleep, mock_get):
        mock_429 = MagicMock()
        mock_429.status_code = 429

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"ok": True}

        mock_get.side_effect = [mock_429, mock_200]

        client = _make_client()
        result = client._request("https://api.wallapop.com/test", {})

        assert result == {"ok": True}
        assert mock_sleep.call_count >= 1

    @patch("src.extractors.base_client.requests.get")
    def test_timeout_retries(self, mock_get):
        import requests
        mock_get.side_effect = [
            requests.exceptions.Timeout(),
            MagicMock(status_code=200, json=lambda: {"ok": True}),
        ]

        client = _make_client()
        result = client._request("https://api.wallapop.com/test", {}, max_retries=2)

        assert result == {"ok": True}


class TestRandomDelay:

    def test_delay_within_bounds(self):
        client = _make_client()
        for _ in range(100):
            delay = client._random_delay()
            assert client.DELAY_MIN <= delay <= client.DELAY_MAX
