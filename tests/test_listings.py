"""Tests for listings extractor anti-oscillation and stats accumulation."""
import pytest
from src.extractors.listings import (
    WallapopListingsExtractor, ListingData, StoredListing, ListingUpdate,
)


def _listing(lpn="LPN001", pid="pid-a", **kw):
    defaults = {
        "account_id": 1, "product_id": pid, "title": "Test",
        "conversations_count": 10, "favorites_count": 5, "views_count": 100,
    }
    defaults.update(kw)
    return ListingData(lpn=lpn, **defaults)


def _stored(lpn="LPN001", pid="pid-a", prev=None, **kw):
    defaults = {
        "conversations_count": 10, "favorites_count": 5, "views_count": 100,
        "conversations_accumulated": 0, "favorites_accumulated": 0, "views_accumulated": 0,
    }
    defaults.update(kw)
    return StoredListing(lpn=lpn, product_id=pid, previous_product_id=prev, **defaults)


class TestAntiOscillation:
    def test_same_pid_unchanged(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-a")
        existing = {"LPN001": _stored(pid="pid-a")}
        seen = {}
        result = ext._process_listing(parsed, existing, seen)
        assert result is None  # unchanged
        assert ext._stats["unchanged"] == 1

    def test_new_pid_triggers_accumulation(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-b")
        existing = {"LPN001": _stored(pid="pid-a")}
        seen = {}
        result = ext._process_listing(parsed, existing, seen)
        assert result.action == "product_id_changed"
        assert result.new_conversations_accumulated == 10
        assert result.new_favorites_accumulated == 5
        assert result.new_views_accumulated == 100
        assert result.new_previous_product_id == "pid-a"

    def test_oscillation_detected(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-a")
        existing = {"LPN001": _stored(pid="pid-b", prev="pid-a")}
        seen = {}
        result = ext._process_listing(parsed, existing, seen)
        assert result.action == "updated"
        assert result.new_previous_product_id == "pid-b"
        assert ext._stats["product_id_changes"] == 0

    def test_per_run_dedup(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed1 = _listing(pid="pid-a")
        parsed2 = _listing(pid="pid-b")
        seen = {}
        ext._process_listing(parsed1, {}, seen)
        result = ext._process_listing(parsed2, {}, seen)
        assert result is None

    def test_new_listing(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing()
        result = ext._process_listing(parsed, {}, {})
        assert result.action == "new"

    def test_stats_change_triggers_update(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-a", conversations_count=20)
        existing = {"LPN001": _stored(pid="pid-a", conversations_count=10)}
        result = ext._process_listing(parsed, existing, {})
        assert result.action == "updated"

    def test_flag_change_triggers_update(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-a", is_reserved=True)
        existing = {"LPN001": _stored(pid="pid-a")}
        result = ext._process_listing(parsed, existing, {})
        assert result.action == "updated"

    def test_accumulation_with_previous_accumulated(self):
        ext = WallapopListingsExtractor("t", ["ua"], account_id=1)
        parsed = _listing(pid="pid-c")
        existing = {
            "LPN001": _stored(
                pid="pid-b",
                conversations_count=15,
                conversations_accumulated=30,
                favorites_count=8,
                favorites_accumulated=20,
                views_count=200,
                views_accumulated=500,
            )
        }
        result = ext._process_listing(parsed, existing, {})
        assert result.new_conversations_accumulated == 45
        assert result.new_favorites_accumulated == 28
        assert result.new_views_accumulated == 700
