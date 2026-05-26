"""
Wallapop chats/conversations extractor.

Extracts messaging conversations from Wallapop's inbox API with:
- Token-based pagination (next_from, not offset-based)
- Change detection: only updates conversations whose monitored fields changed
- Cross-account duplicate handling: same conversation may appear under
  different accounts — merges timestamps, keeping the most recent
- Per-run dedup to avoid processing the same conversation twice

Monitored fields (COMPARABLE_FIELDS):
  total_messages, unread_messages, is_sold, last_message_timestamp,
  item_status, item_price
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .base_client import WallapopAPIClient

logger = logging.getLogger(__name__)


COMPARABLE_FIELDS = [
    "total_messages", "unread_messages", "is_sold",
    "last_message_timestamp", "item_status", "item_price",
]


@dataclass
class ConversationData:
    """Parsed conversation from Wallapop API."""
    conversation_hash: str
    account_id: int
    item_hash: Optional[str] = None
    item_title: Optional[str] = None
    item_price: Optional[int] = None
    item_status: Optional[str] = None
    item_category_id: Optional[int] = None
    item_image_url: Optional[str] = None
    item_slug: Optional[str] = None
    topic_id: Optional[str] = None
    total_messages: int = 0
    unread_messages: int = 0
    is_sold: bool = False
    last_message_timestamp: Optional[datetime] = None


@dataclass
class StoredConversation:
    """Represents a conversation already in the database for comparison."""
    conversation_hash: str
    total_messages: int = 0
    unread_messages: int = 0
    is_sold: bool = False
    last_message_timestamp: Optional[datetime] = None
    item_status: Optional[str] = None
    item_price: Optional[int] = None


class WallapopChatsExtractor(WallapopAPIClient):
    """
    Extracts conversations from Wallapop inbox with change detection
    and cross-account duplicate handling.
    """

    INBOX_ENDPOINT = "/bff/messaging/inbox"
    DELAY_BETWEEN_REQUESTS = 0.1
    MAX_CONVERSATIONS = 100_000
    MAX_PAGES_WITHOUT_NEW = 100

    def __init__(self, bearer_token: str, user_agents: list[str], account_id: int = 0, **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self._account_id = account_id
        self._stats = {
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "processed": 0,
            "api_calls": 0,
            "errors": 0,
        }

    def extract(
        self,
        existing_conversations: Optional[dict[str, StoredConversation]] = None,
        on_new: Optional[Any] = None,
        on_update: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Extract conversations for this account.

        Args:
            existing_conversations: Dict mapping conversation_hash → StoredConversation.
            on_new: Optional callback(ConversationData) for new conversations.
            on_update: Optional callback(conversation_hash, ConversationData, changed_fields)
                for updated conversations.
        """
        if existing_conversations is None:
            existing_conversations = {}

        known_hashes: set[str] = set(existing_conversations.keys())
        results: list[ConversationData] = []

        next_from: Optional[str] = None
        pages_without_new = 0
        total_processed = 0

        while total_processed < self.MAX_CONVERSATIONS:
            if pages_without_new >= self.MAX_PAGES_WITHOUT_NEW:
                logger.info("Stopping: %d pages without new conversations", self.MAX_PAGES_WITHOUT_NEW)
                break

            page_data = self._fetch_inbox_page(next_from)
            self._stats["api_calls"] += 1

            if page_data is None:
                if self._refresh_callback:
                    new_token = self._refresh_callback()
                    if new_token:
                        self._bearer = new_token
                        logger.info("Bearer renewed, retrying")
                        continue
                logger.error("Could not fetch inbox, stopping")
                break

            conversations_raw = page_data.get("conversations", []) or []
            if not conversations_raw:
                logger.info("No more conversations found")
                break

            page_new = 0

            for conv_raw in conversations_raw:
                parsed = self._parse_conversation(conv_raw)
                if not parsed:
                    continue

                total_processed += 1
                self._stats["processed"] += 1
                conv_hash = parsed.conversation_hash

                if conv_hash not in known_hashes:
                    known_hashes.add(conv_hash)
                    self._stats["new"] += 1
                    page_new += 1
                    results.append(parsed)
                    if on_new:
                        on_new(parsed)

                elif self._has_changed(conv_hash, parsed, existing_conversations):
                    changed = self._get_changed_fields(conv_hash, parsed, existing_conversations)
                    self._stats["updated"] += 1
                    results.append(parsed)
                    if on_update:
                        on_update(conv_hash, parsed, changed)

                    existing_conversations[conv_hash] = self._to_stored(parsed)
                else:
                    self._stats["unchanged"] += 1

            if page_new == 0:
                pages_without_new += 1
            else:
                pages_without_new = 0

            next_from = page_data.get("next_from")
            if not next_from:
                logger.info("End of pagination (no next_from)")
                break

            time.sleep(self.DELAY_BETWEEN_REQUESTS)

        logger.info(
            "Chats extraction: %d new, %d updated, %d unchanged, %d processed",
            self._stats["new"], self._stats["updated"],
            self._stats["unchanged"], self._stats["processed"],
        )
        return {"results": results, "stats": self._stats}

    def _fetch_inbox_page(
        self,
        next_from: Optional[str] = None,
        page_size: int = 30,
        max_messages: int = 30,
    ) -> Optional[dict]:
        url = f"{self.BASE_URL}{self.INBOX_ENDPOINT}"
        headers = self._build_headers()
        params: dict[str, Any] = {"page_size": page_size, "max_messages": max_messages}
        if next_from:
            params["from"] = next_from
        return self._request(url, headers, params)

    def _parse_conversation(self, conv_data: dict) -> Optional[ConversationData]:
        """
        Parse a single conversation from API response.

        Handles nested structures: item.price.amount, messages.messages[],
        and converts epoch-ms timestamps to datetime UTC.
        """
        try:
            conv_hash = conv_data.get("hash")
            if not conv_hash:
                return None

            item = conv_data.get("item", {}) or {}

            item_price = None
            price_data = item.get("price", {}) or {}
            if isinstance(price_data, dict):
                amount = price_data.get("amount")
                if amount is not None:
                    try:
                        item_price = int(round(float(amount)))
                    except (ValueError, TypeError):
                        pass

            item_status = item.get("status")
            is_sold = str(item_status).lower() == "sold" if item_status else False

            messages_obj = conv_data.get("messages", {}) or {}
            msgs = messages_obj.get("messages", []) if isinstance(messages_obj, dict) else []
            total_messages = len(msgs)

            last_ts = None
            if msgs:
                try:
                    max_ts_ms = max(
                        m.get("timestamp") for m in msgs
                        if m and m.get("timestamp") is not None
                    )
                    if max_ts_ms is not None:
                        last_ts = datetime.fromtimestamp(max_ts_ms / 1000.0, tz=timezone.utc)
                except (ValueError, TypeError):
                    pass

            return ConversationData(
                conversation_hash=conv_hash,
                account_id=self._account_id,
                item_hash=item.get("hash"),
                item_title=item.get("title"),
                item_price=item_price,
                item_status=item_status,
                item_category_id=item.get("category_id"),
                item_image_url=item.get("image_url"),
                item_slug=item.get("slug"),
                topic_id=conv_data.get("topic_id"),
                total_messages=total_messages,
                unread_messages=conv_data.get("unread_messages", 0) or 0,
                is_sold=is_sold,
                last_message_timestamp=last_ts,
            )
        except Exception as e:
            logger.error("Error parsing conversation: %s", e)
            self._stats["errors"] += 1
            return None

    def _has_changed(
        self,
        conv_hash: str,
        new_data: ConversationData,
        existing: dict[str, StoredConversation],
    ) -> bool:
        if conv_hash not in existing:
            return True
        stored = existing[conv_hash]
        for f in COMPARABLE_FIELDS:
            if getattr(stored, f, None) != getattr(new_data, f, None):
                return True
        return False

    def _get_changed_fields(
        self,
        conv_hash: str,
        new_data: ConversationData,
        existing: dict[str, StoredConversation],
    ) -> list[str]:
        if conv_hash not in existing:
            return COMPARABLE_FIELDS
        stored = existing[conv_hash]
        changed = []
        for f in COMPARABLE_FIELDS:
            if getattr(stored, f, None) != getattr(new_data, f, None):
                changed.append(f)
        return changed

    @staticmethod
    def _to_stored(conv: ConversationData) -> StoredConversation:
        return StoredConversation(
            conversation_hash=conv.conversation_hash,
            total_messages=conv.total_messages,
            unread_messages=conv.unread_messages,
            is_sold=conv.is_sold,
            last_message_timestamp=conv.last_message_timestamp,
            item_status=conv.item_status,
            item_price=conv.item_price,
        )
