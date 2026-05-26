"""Tests for the orders extractor."""
from src.extractors.orders import (
    WallapopOrdersExtractor,
    OrderResult,
    WALLAPOP_TO_INTERNAL,
    LPN_PATTERN,
)


class TestStatusMapping:

    def test_all_statuses_map(self):
        for wallapop_status, internal in WALLAPOP_TO_INTERNAL.items():
            assert internal in {
                "PENDING_SHIPMENT", "SHIPPED", "DELIVERED",
                "INCIDENT", "RETURNING", "CANCELLED",
            }, f"Unexpected internal status {internal} for {wallapop_status}"

    def test_shipped_states(self):
        shipped = [k for k, v in WALLAPOP_TO_INTERNAL.items() if v == "SHIPPED"]
        assert len(shipped) >= 6  # IN_TRANSIT, DELIVERED_TO_CARRIER, etc.

    def test_incident_states(self):
        incidents = [k for k, v in WALLAPOP_TO_INTERNAL.items() if v == "INCIDENT"]
        assert "DISPUTE_OPEN" in incidents
        assert "DISPUTE_ESCALATED" in incidents


class TestLPNExtraction:

    def test_extract_lpn_from_description(self):
        match = LPN_PATTERN.search("LPNAB123456 Nintendo Switch Lite")
        assert match is not None
        assert match.group() == "LPNAB123456"

    def test_no_lpn(self):
        assert LPN_PATTERN.search("Product without LPN") is None

    def test_lpn_in_middle(self):
        match = LPN_PATTERN.search("Item: LPNXY999888 condition: good")
        assert match is not None
        assert match.group() == "LPNXY999888"


class TestOrderResult:

    def test_dataclass_defaults(self):
        result = OrderResult(request_id="req-1", status="IN_TRANSIT", internal_status="SHIPPED")
        assert result.is_bundle is False
        assert result.bundle_items == []
        assert result.lpn is None

    def test_bundle_order(self):
        result = OrderResult(
            request_id="req-2",
            status="DELIVERED",
            internal_status="DELIVERED",
            is_bundle=True,
            bundle_items=[{"id": "item-1"}, {"id": "item-2"}],
        )
        assert result.is_bundle is True
        assert len(result.bundle_items) == 2
