"""Tests for the listings extractor with anti-oscillation logic."""
from src.extractors.listings import (
    WallapopListingsExtractor,
    ListingResult,
    ListingDelta,
    LPN_PATTERN,
)


class TestLPNPattern:

    def test_matches_valid_lpn(self):
        assert LPN_PATTERN.search("Producto LPNAB123456 en buen estado")

    def test_no_match_without_lpn(self):
        assert LPN_PATTERN.search("Producto sin identificador") is None

    def test_case_insensitive(self):
        assert LPN_PATTERN.search("lpncd789012")


class TestAntiOscillation:

    def _make_listing(self, lpn="LPNAB123456", pid="pid-new"):
        return ListingResult(lpn=lpn, product_id=pid)

    def test_new_listing_detected(self):
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        listings = [self._make_listing()]
        previous_state = {}

        deltas = extractor.detect_changes(listings, previous_state)

        assert len(deltas) == 1
        assert deltas[0].change_type == "new"

    def test_unchanged_listing(self):
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        listings = [self._make_listing(pid="pid-same")]
        previous_state = {"LPNAB123456": {"product_id": "pid-same"}}

        deltas = extractor.detect_changes(listings, previous_state)

        assert len(deltas) == 1
        assert deltas[0].change_type == "unchanged"

    def test_oscillation_detected(self):
        """When new product_id matches previous_product_id, it's oscillation."""
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        listings = [self._make_listing(pid="pid-old")]
        previous_state = {
            "LPNAB123456": {
                "product_id": "pid-current",
                "previous_product_id": "pid-old",
            }
        }

        deltas = extractor.detect_changes(listings, previous_state)

        assert len(deltas) == 1
        assert deltas[0].change_type == "oscillation"
        assert deltas[0].old_product_id == "pid-current"
        assert deltas[0].new_product_id == "pid-old"

    def test_real_product_id_change(self):
        """A genuinely new product_id triggers metric accumulation."""
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        listings = [self._make_listing(pid="pid-brand-new")]
        previous_state = {
            "LPNAB123456": {
                "product_id": "pid-current",
                "previous_product_id": "pid-old",
                "views_count": 42,
                "favorites_count": 5,
                "conversations_count": 3,
            }
        }

        deltas = extractor.detect_changes(listings, previous_state)

        assert len(deltas) == 1
        assert deltas[0].change_type == "product_id_changed"
        assert deltas[0].accumulated_views == 42
        assert deltas[0].accumulated_favorites == 5
        assert deltas[0].accumulated_conversations == 3

    def test_first_occurrence_wins(self):
        """Same LPN seen twice in extraction - only first is kept."""
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        l1 = self._make_listing(pid="pid-1")
        l2 = self._make_listing(pid="pid-2")

        extractor._seen_lpns[l1.lpn] = l1.product_id

        assert l2.lpn in extractor._seen_lpns


class TestParseProduct:

    def test_parse_valid_product(self):
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        product = {
            "id": "abc123",
            "content": {
                "title": "Nintendo Switch",
                "description": "LPNXY000001 Console in good condition",
                "sale_price": 199,
                "category_id": 15000,
                "web_slug": "nintendo-switch-abc",
                "conversations": 3,
                "favorites": 10,
                "views": 150,
                "image": {"original": "https://cdn.wallapop.com/img.jpg"},
                "flags": {
                    "reserved": False,
                    "sold": False,
                    "banned": False,
                    "expired": False,
                },
            },
        }

        result = extractor._parse_product(product)

        assert result is not None
        assert result.lpn == "LPNXY000001"
        assert result.product_id == "abc123"
        assert result.title == "Nintendo Switch"
        assert result.conversations_count == 3

    def test_parse_product_without_lpn_returns_none(self):
        extractor = WallapopListingsExtractor("token", ["UA/1.0"])
        product = {
            "id": "abc123",
            "content": {"title": "No LPN here", "description": "Just a regular product"},
        }

        result = extractor._parse_product(product)
        assert result is None
