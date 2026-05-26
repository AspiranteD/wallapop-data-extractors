"""
Bearer token manager for Wallapop API authentication.

Wallapop uses short-lived bearer tokens derived from browser cookies.
This module manages token lifecycle including refresh and validation.
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccountCredentials:
    """Credentials for a single Wallapop account."""
    account_id: str
    account_name: str
    bearer_token: str
    user_hash: Optional[str] = None
    expires_at: Optional[float] = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class TokenManager:
    """
    Manages bearer tokens for multiple Wallapop accounts.

    Supports:
    - Multi-account token storage
    - Automatic expiration detection
    - Token refresh via callback
    - Account validation (user_hash check)
    """

    def __init__(
        self,
        refresh_callback: Optional[Callable[[str], Optional[str]]] = None,
    ):
        """
        Args:
            refresh_callback: Function that takes an account_id and returns
                a new bearer token, or None if refresh failed.
        """
        self._accounts: dict[str, AccountCredentials] = {}
        self._refresh_callback = refresh_callback

    def register_account(self, credentials: AccountCredentials):
        """Register or update an account's credentials."""
        self._accounts[credentials.account_id] = credentials
        logger.info("Registered account %s (%s)", credentials.account_name, credentials.account_id)

    def get_token(self, account_id: str) -> Optional[str]:
        """
        Get a valid bearer token for an account.

        If the token is expired and a refresh callback is configured,
        attempts automatic refresh.
        """
        creds = self._accounts.get(account_id)
        if not creds:
            logger.warning("Account %s not registered", account_id)
            return None

        if creds.is_expired:
            logger.info("Token expired for %s, attempting refresh", creds.account_name)
            return self._try_refresh(account_id)

        return creds.bearer_token

    def invalidate(self, account_id: str) -> Optional[str]:
        """Mark a token as expired and try to refresh it."""
        creds = self._accounts.get(account_id)
        if creds:
            creds.expires_at = 0
            return self._try_refresh(account_id)
        return None

    def get_active_accounts(self) -> list[AccountCredentials]:
        """Return all accounts with non-expired tokens."""
        return [c for c in self._accounts.values() if not c.is_expired]

    def _try_refresh(self, account_id: str) -> Optional[str]:
        if not self._refresh_callback:
            logger.warning("No refresh callback configured")
            return None

        new_token = self._refresh_callback(account_id)
        if new_token:
            self._accounts[account_id].bearer_token = new_token
            self._accounts[account_id].expires_at = None
            logger.info("Token refreshed for %s", self._accounts[account_id].account_name)
            return new_token

        logger.error("Token refresh failed for account %s", account_id)
        return None

    def __len__(self) -> int:
        return len(self._accounts)
