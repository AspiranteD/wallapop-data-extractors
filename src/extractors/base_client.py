"""
Abstract base client for Wallapop API interactions.

Provides common functionality shared across all extractors:
- Bearer token authentication with automatic renewal
- Realistic header generation with randomized fingerprinting
- Exponential backoff retry logic with rate-limit handling (429)
- Random delays between requests to mimic human behavior
"""
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)


class WallapopAPIClient(ABC):
    """
    Abstract base for all Wallapop API extractors.

    Subclasses implement `extract()` to define specific extraction logic.
    The base class handles authentication, headers, retries, and rate limiting.
    """

    BASE_URL = "https://api.wallapop.com"
    MAX_RETRIES = 3
    REQUEST_TIMEOUT = 30
    DELAY_MIN = 0.05
    DELAY_MAX = 0.3

    APP_VERSIONS = ["6.57.0", "6.56.0", "6.55.0", "6.58.0", "6.57.1"]
    ACCEPT_LANGUAGES = [
        "es-ES,es;q=0.9,en;q=0.8",
        "es-ES,es;q=0.9",
        "es,en;q=0.9",
        "es-ES,es;q=0.9,ca;q=0.8,en;q=0.7",
    ]
    REFERER_URLS = [
        "https://es.wallapop.com/",
        "https://es.wallapop.com/app/",
        "https://es.wallapop.com/search",
        "https://es.wallapop.com/user/",
        "https://es.wallapop.com/item/",
    ]

    def __init__(
        self,
        bearer_token: str,
        user_agents: list[str],
        token_refresh_callback: Optional[Callable[[], Optional[str]]] = None,
    ):
        """
        Args:
            bearer_token: Initial bearer token for authentication.
            user_agents: Pool of user-agent strings for rotation.
            token_refresh_callback: Optional callable that returns a new bearer
                token when the current one expires (401). If None, extraction
                stops on token expiration.
        """
        self._bearer = bearer_token
        self._user_agents = user_agents
        self._refresh_callback = token_refresh_callback
        self.last_error: Optional[str] = None

    @property
    def bearer(self) -> str:
        return self._bearer

    def _generate_device_id(self) -> str:
        chars = "0123456789abcdef"
        suffix = "".join(random.choice(chars) for _ in range(16))
        return f"android-{suffix}"

    def _build_headers(self, endpoint_type: str = "default") -> dict[str, str]:
        """
        Build HTTP headers with realistic randomization.

        Different endpoint types get different header profiles to match
        what the real Wallapop app sends.
        """
        headers = {
            "User-Agent": random.choice(self._user_agents),
            "Authorization": f"Bearer {self._bearer}",
            "Accept": "application/json",
            "Accept-Language": random.choice(self.ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        if random.random() < 0.7:
            headers["Referer"] = random.choice(self.REFERER_URLS)

        if endpoint_type in ("details", "vertical"):
            headers["X-AppVersion"] = "81030"
            headers["X-DeviceOS"] = "0"
        else:
            headers["X-Device-ID"] = self._generate_device_id()
            headers["X-Platform"] = "android"
            headers["X-App-Version"] = random.choice(self.APP_VERSIONS)

        if random.random() < 0.3:
            headers["Cache-Control"] = random.choice(["no-cache", "max-age=0", "no-store"])

        return headers

    def _random_delay(self) -> float:
        """Human-like delay using biased distribution (70% short, 30% longer)."""
        if random.random() < 0.7:
            return self.DELAY_MIN + (self.DELAY_MAX - self.DELAY_MIN) * random.uniform(0, 0.4)
        return self.DELAY_MIN + (self.DELAY_MAX - self.DELAY_MIN) * random.uniform(0.4, 1.0)

    def _request(
        self,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[dict]:
        """
        GET request with automatic retries and rate-limit handling.

        Returns parsed JSON on success, None on failure.
        On 401, attempts token refresh via callback before giving up.
        On 429, uses exponential backoff with jitter.
        """
        retries = max_retries or self.MAX_RETRIES
        self.last_error = None

        for attempt in range(retries):
            try:
                response = requests.get(
                    url, headers=headers, params=params, timeout=self.REQUEST_TIMEOUT,
                )

                if response.status_code == 200:
                    if random.random() < 0.8:
                        time.sleep(self._random_delay())
                    return response.json()

                if response.status_code == 401:
                    self.last_error = "HTTP 401 (token expired)"
                    if self._refresh_callback:
                        new_token = self._refresh_callback()
                        if new_token:
                            self._bearer = new_token
                            headers["Authorization"] = f"Bearer {new_token}"
                            logger.info("Bearer token refreshed, retrying")
                            continue
                    return None

                if response.status_code == 429:
                    base_delay = 2 ** attempt
                    backoff = base_delay + random.uniform(0, base_delay * 0.3)
                    logger.warning("Rate limited (429), backing off %.1fs", backoff)
                    time.sleep(backoff)
                    continue

                logger.error("HTTP %d for %s", response.status_code, url)
                if attempt < retries - 1:
                    time.sleep(1)

            except requests.exceptions.Timeout:
                logger.warning("Timeout attempt %d/%d for %s", attempt + 1, retries, url)
                if attempt < retries - 1:
                    time.sleep(1)
            except Exception as e:
                logger.error("Request error: %s", e)
                if attempt < retries - 1:
                    time.sleep(1)

        self.last_error = f"Failed after {retries} retries"
        return None

    @abstractmethod
    def extract(self, **kwargs) -> dict[str, Any]:
        """Run the extraction. Returns a results dict."""
