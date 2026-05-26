"""Tests for chats extractor change detection."""
import pytest
from datetime import datetime, timezone
from src.extractors.chats import (
    WallapopChatsExtractor, ConversationData, StoredConversation, COMPARABLE_FIELDS,
)


def _stored(hash="conv1", msgs=5, unread=0, sold=False, ts=None, status="active", price=100):
    return StoredConversation(
        conversation_hash=hash,
        total_messages=msgs,
        unread_messages=unread,
        is_sold=sold,
        last_message_timestamp=ts,
        item_status=status,
        item_price=price,
    )


def _parsed(hash="conv1", msgs=5, unread=0, sold=False, ts=None, status="active", price=100):
    return ConversationData(
        conversation_hash=hash,
        account_id=1,
        total_messages=msgs,
        unread_messages=unread,
        is_sold=sold,
        last_message_timestamp=ts,
        item_status=status,
        item_price=price,
    )


class TestChangeDetection:
    def test_no_change(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        existing = {"conv1": _stored()}
        assert ext._has_changed("conv1", _parsed(), existing) is False

    def test_new_message(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        existing = {"conv1": _stored(msgs=5)}
        assert ext._has_changed("conv1", _parsed(msgs=6), existing) is True

    def test_sold_change(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        existing = {"conv1": _stored(sold=False)}
        assert ext._has_changed("conv1", _parsed(sold=True), existing) is True

    def test_price_change(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        existing = {"conv1": _stored(price=100)}
        assert ext._has_changed("conv1", _parsed(price=90), existing) is True

    def test_unknown_hash_is_changed(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        assert ext._has_changed("new", _parsed(hash="new"), {}) is True


class TestChangedFields:
    def test_identifies_changed(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        existing = {"conv1": _stored(msgs=5, price=100)}
        changed = ext._get_changed_fields("conv1", _parsed(msgs=6, price=90), existing)
        assert "total_messages" in changed
        assert "item_price" in changed
        assert "is_sold" not in changed


class TestConversationParsing:
    def test_parse_full(self):
        ext = WallapopChatsExtractor("t", ["ua"], account_id=5)
        raw = {
            "hash": "conv-abc",
            "item": {
                "hash": "item-123",
                "title": "Widget",
                "status": "sold",
                "price": {"amount": 49.99},
                "category_id": 100,
                "image_url": "https://img.jpg",
                "slug": "widget-slug",
            },
            "messages": {
                "messages": [
                    {"timestamp": 1700000000000},
                    {"timestamp": 1700000060000},
                ]
            },
            "unread_messages": 1,
            "topic_id": "topic-1",
        }
        parsed = ext._parse_conversation(raw)
        assert parsed is not None
        assert parsed.conversation_hash == "conv-abc"
        assert parsed.item_price == 50
        assert parsed.is_sold is True
        assert parsed.total_messages == 2
        assert parsed.unread_messages == 1
        assert parsed.last_message_timestamp is not None

    def test_parse_missing_hash(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        assert ext._parse_conversation({}) is None

    def test_parse_no_messages(self):
        ext = WallapopChatsExtractor("t", ["ua"])
        parsed = ext._parse_conversation({"hash": "h1", "item": {}})
        assert parsed is not None
        assert parsed.total_messages == 0
        assert parsed.last_message_timestamp is None
