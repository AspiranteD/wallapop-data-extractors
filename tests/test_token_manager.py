"""Tests for the token manager."""
import time

from src.auth.token_manager import TokenManager, AccountCredentials


class TestTokenManager:

    def test_register_and_get(self):
        mgr = TokenManager()
        mgr.register_account(AccountCredentials(
            account_id="acc-1", account_name="Test Account", bearer_token="token-123",
        ))
        assert mgr.get_token("acc-1") == "token-123"

    def test_unknown_account_returns_none(self):
        mgr = TokenManager()
        assert mgr.get_token("nonexistent") is None

    def test_expired_token_triggers_refresh(self):
        refreshed = []
        def mock_refresh(account_id):
            refreshed.append(account_id)
            return "new-token"

        mgr = TokenManager(refresh_callback=mock_refresh)
        mgr.register_account(AccountCredentials(
            account_id="acc-1", account_name="Test", bearer_token="old-token", expires_at=0,
        ))

        token = mgr.get_token("acc-1")
        assert token == "new-token"
        assert "acc-1" in refreshed

    def test_non_expired_token_no_refresh(self):
        refreshed = []
        def mock_refresh(account_id):
            refreshed.append(account_id)
            return "new-token"

        mgr = TokenManager(refresh_callback=mock_refresh)
        mgr.register_account(AccountCredentials(
            account_id="acc-1", account_name="Test", bearer_token="valid-token",
            expires_at=time.time() + 3600,
        ))

        token = mgr.get_token("acc-1")
        assert token == "valid-token"
        assert len(refreshed) == 0

    def test_invalidate_triggers_refresh(self):
        def mock_refresh(account_id):
            return "refreshed-token"

        mgr = TokenManager(refresh_callback=mock_refresh)
        mgr.register_account(AccountCredentials(
            account_id="acc-1", account_name="Test", bearer_token="token",
        ))

        new_token = mgr.invalidate("acc-1")
        assert new_token == "refreshed-token"

    def test_get_active_accounts(self):
        mgr = TokenManager()
        mgr.register_account(AccountCredentials(
            account_id="active", account_name="Active", bearer_token="t1",
        ))
        mgr.register_account(AccountCredentials(
            account_id="expired", account_name="Expired", bearer_token="t2", expires_at=0,
        ))

        active = mgr.get_active_accounts()
        assert len(active) == 1
        assert active[0].account_id == "active"

    def test_refresh_failure_returns_none(self):
        def mock_refresh(account_id):
            return None

        mgr = TokenManager(refresh_callback=mock_refresh)
        mgr.register_account(AccountCredentials(
            account_id="acc-1", account_name="Test", bearer_token="old", expires_at=0,
        ))

        assert mgr.get_token("acc-1") is None
