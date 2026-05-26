"""Tests for WallapopAPIClient base class."""
import pytest
from unittest.mock import patch, MagicMock
from src.extractors.base_client import WallapopAPIClient


class ConcreteClient(WallapopAPIClient):
    def extract(self, **kwargs):
        return {}


@pytest.fixture
def client():
    return ConcreteClient(
        bearer_token="test-token",
        user_agents=["TestAgent/1.0", "TestAgent/2.0"],
    )


class TestHeaders:
    def test_default_headers(self, client):
        headers = client._build_headers()
        assert "Bearer test-token" in headers["Authorization"]
        assert headers["Accept"] == "application/json"
        assert "X-Device-ID" in headers
        assert headers["X-Platform"] == "android"

    def test_details_headers(self, client):
        headers = client._build_headers(endpoint_type="details")
        assert headers["X-AppVersion"] == "81030"
        assert "X-Device-ID" not in headers

    def test_vertical_headers(self, client):
        headers = client._build_headers(endpoint_type="vertical")
        assert headers["X-DeviceOS"] == "0"


class TestDeviceID:
    def test_format(self, client):
        did = client._generate_device_id()
        assert did.startswith("android-")
        assert len(did) == len("android-") + 16

    def test_randomized(self, client):
        ids = {client._generate_device_id() for _ in range(10)}
        assert len(ids) > 1


class TestRandomDelay:
    def test_within_bounds(self, client):
        for _ in range(100):
            delay = client._random_delay()
            assert client.DELAY_MIN <= delay <= client.DELAY_MAX + 0.01


class TestRequest:
    @patch("src.extractors.base_client.requests.get")
    def test_success(self, mock_get, client):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"data": "ok"}
        mock_get.return_value = mock_resp

        result = client._request("https://api.example.com", {})
        assert result == {"data": "ok"}

    @patch("src.extractors.base_client.requests.get")
    def test_401_no_callback(self, mock_get, client):
        mock_resp = MagicMock(status_code=401)
        mock_get.return_value = mock_resp

        result = client._request("https://api.example.com", {})
        assert result is None
        assert "401" in client.last_error

    @patch("src.extractors.base_client.requests.get")
    def test_401_with_callback(self, mock_get):
        refreshed = MagicMock(status_code=200)
        refreshed.json.return_value = {"ok": True}
        mock_get.side_effect = [MagicMock(status_code=401), refreshed]

        client = ConcreteClient(
            bearer_token="old",
            user_agents=["UA/1"],
            token_refresh_callback=lambda: "new-token",
        )
        result = client._request("https://api.example.com", {"Authorization": "Bearer old"})
        assert result == {"ok": True}
        assert client.bearer == "new-token"

    @patch("src.extractors.base_client.requests.get")
    def test_429_retries(self, mock_get, client):
        rate_limited = MagicMock(status_code=429)
        success = MagicMock(status_code=200)
        success.json.return_value = {"retry": "ok"}
        mock_get.side_effect = [rate_limited, success]

        result = client._request("https://api.example.com", {})
        assert result == {"retry": "ok"}

    @patch("src.extractors.base_client.requests.get")
    def test_timeout(self, mock_get, client):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout()
        result = client._request("https://api.example.com", {}, max_retries=1)
        assert result is None
