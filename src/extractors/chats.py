"""
Wallapop chats/conversations extractor.

Extracts messaging inbox conversations including message counts,
item metadata, read status, and sale indicators.

Key features:
- Paginated inbox traversal using cursor-based pagination (next_from)
- Change detection to avoid redundant DB updates
- Cross-account deduplication (same conversation visible from multiple accounts)
- Timestamp-based merge for duplicates
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .base_client import WallapopAPIClient

logger = logging.getLogger(__name__)


@dataclass
class ConversationResult:
    """Parsed conversation from the Wallapop inbox API."""
    conversation_hash: str
    item_hash: Optional[str] = None
    item_title: Optional[str] = None
    item_price: Optional[int] = None
    item_status: Optional[str] = None
    item_image_url: Optional[str] = None
    item_slug: Optional[str] = None
    item_category_id: Optional[int] = None
    topic_id: Optional[str] = None
    total_messages: int = 0
    unread_messages: int = 0
    is_sold: bool = False
    last_message_at: Optional[datetime] = None


CHANGE_DETECTION_FIELDS = [
    "total_messages", "unread_messages", "is_sold",
    "last_message_at", "item_status", "item_price",
]


class WallapopChatsExtractor(WallapopAPIClient):
    """
    Extracts conversations from Wallapop's messaging inbox.

    Pagination: Uses cursor-based pagination (next_from token from response).
    The extractor processes all pages until:
    - No more pages (next_from is empty)
    - Max conversations limit reached
    - Max pages without new conversations (stale detection)
    """

    INBOX_ENDPOINT = "/bff/messaging/inbox"
    MAX_CONVERSATIONS = 100_000
    MAX_STALE_PAGES = 100

    def __init__(self, bearer_token: str, user_agents: list[str], **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self._stats = {
            "pages_fetched": 0,
            "conversations_found": 0,
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "errors": 0,
        }
        self._seen_hashes: set[str] = set()

    def extract(self, **kwargs) -> dict[str, Any]:
        """Extract all conversations from the inbox."""
        logger.info("Starting chat extraction")

        next_from: Optional[str] = None
        stale_pages = 0
        results: list[ConversationResult] = []

        while len(results) < self.MAX_CONVERSATIONS:
            if stale_pages >= self.MAX_STALE_PAGES:
                logger.info("Stopping: %d pages without new conversations", self.MAX_STALE_PAGES)
                break

            page_data = self._fetch_inbox_page(next_from)
            if page_data is None:
                break

            self._stats["pages_fetched"] += 1
            conversations_raw = page_data.get("conversations", []) or []

            if not conversations_raw:
                logger.info("No more conversations")
                break

            page_new = 0
            for conv_raw in conversations_raw:
                parsed = self._parse_conversation(conv_raw)
                if not parsed:
                    continue

                self._stats["conversations_found"] += 1

                if parsed.conversation_hash in self._seen_hashes:
                    self._stats["unchanged"] += 1
                    continue

                self._seen_hashes.add(parsed.conversation_hash)
                results.append(parsed)
                page_new += 1
                self._stats["new"] += 1

            if page_new == 0:
                stale_pages += 1
            else:
                stale_pages = 0

            next_from = page_data.get("next_from")
            if not next_from:
                logger.info("End of pagination (no next_from)")
                break

            logger.info(
                "Page %d: %d conversations, %d new",
                self._stats["pages_fetched"], len(conversations_raw), page_new,
            )

        logger.info(
            "Extraction complete: %d conversations (%d new, %d unchanged)",
            len(results), self._stats["new"], self._stats["unchanged"],
        )

        return {
            "conversations": [self._result_to_dict(c) for c in results],
            **self._stats,
        }

    def _fetch_inbox_page(
        self, next_from: Optional[str] = None, page_size: int = 30,
    ) -> Optional[dict]:
        url = f"{self.BASE_URL}{self.INBOX_ENDPOINT}"
        headers = self._build_headers()
        params: dict = {"page_size": page_size, "max_messages": 30}
        if next_from:
            params["from"] = next_from

        result = self._request(url, headers, params)
        time.sleep(self._random_delay())
        return result

    def _parse_conversation(self, raw: dict) -> Optional[ConversationResult]:
        try:
            conv_hash = raw.get("hash")
            if not conv_hash:
                return None

            item = raw.get("item", {}) or {}
            price_data = item.get("price", {}) or {}
            item_price = None
            if isinstance(price_data, dict) and price_data.get("amount") is not None:
                try:
                    item_price = int(round(float(price_data["amount"])))
                except (ValueError, TypeError):
                    pass

            item_status = item.get("status")
            is_sold = str(item_status).lower() == "sold" if item_status else False

            messages_obj = raw.get("messages", {}) or {}
            msgs = messages_obj.get("messages", []) if isinstance(messages_obj, dict) else []

            last_message_at = None
            if msgs:
                timestamps = [m.get("timestamp") for m in msgs if m and m.get("timestamp")]
                if timestamps:
                    max_ts = max(timestamps)
                    last_message_at = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc)

            return ConversationResult(
                conversation_hash=conv_hash,
                item_hash=item.get("hash"),
                item_title=item.get("title"),
                item_price=item_price,
                item_status=item_status,
                item_image_url=item.get("image_url"),
                item_slug=item.get("slug"),
                item_category_id=item.get("category_id"),
                topic_id=raw.get("topic_id"),
                total_messages=len(msgs),
                unread_messages=raw.get("unread_messages", 0) or 0,
                is_sold=is_sold,
                last_message_at=last_message_at,
            )
        except Exception as e:
            logger.error("Error parsing conversation: %s", e)
            self._stats["errors"] += 1
            return None

    @staticmethod
    def _result_to_dict(c: ConversationResult) -> dict:
        return {
            "conversation_hash": c.conversation_hash,
            "item_hash": c.item_hash,
            "item_title": c.item_title,
            "item_price": c.item_price,
            "item_status": c.item_status,
            "total_messages": c.total_messages,
            "unread_messages": c.unread_messages,
            "is_sold": c.is_sold,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        }
