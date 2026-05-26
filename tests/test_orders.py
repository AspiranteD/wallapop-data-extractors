"""Tests for orders extractor."""
import pytest
from src.extractors.orders import WallapopOrdersExtractor
from src.parsers.status import WALLAPOP_TO_INTERNAL


class TestBundleHashExtraction:
    def test_extracts_from_wallapop_links(self):
        bundle = {
            "details_info": [
                {"action": {"payload": {"link_url": "wallapop://i/hash1"}}},
                {"action": {"payload": {"link_url": "wallapop://i/hash2"}}},
                {"action": {"payload": {"link_url": "https://not-a-product"}}},
                {"action": {"payload": {}}},
            ]
        }
        hashes = WallapopOrdersExtractor._extract_hashes_from_bundle(bundle)
        assert hashes == ["hash1", "hash2"]

    def test_deduplicates(self):
        bundle = {
            "details_info": [
                {"action": {"payload": {"link_url": "wallapop://i/same"}}},
                {"action": {"payload": {"link_url": "wallapop://i/same"}}},
            ]
        }
        assert len(WallapopOrdersExtractor._extract_hashes_from_bundle(bundle)) == 1

    def test_empty_details(self):
        assert WallapopOrdersExtractor._extract_hashes_from_bundle({"details_info": []}) == []
        assert WallapopOrdersExtractor._extract_hashes_from_bundle({}) == []


class TestStatusMapping:
    def test_all_statuses_mapped(self):
        assert len(WALLAPOP_TO_INTERNAL) >= 18

    def test_por_enviar_states(self):
        for s in ["TRANSACTION_CREATED", "REQUEST_CREATED", "PENDING_TO_MEET"]:
            assert WALLAPOP_TO_INTERNAL[s] == "POR_ENVIAR"

    def test_enviado_states(self):
        shipped = ["DEPOSITED_AT_PUDO", "IN_TRANSIT", "DELIVERED_TO_CARRIER",
                   "ON_HOLD_AT_CARRIER", "ON_HOLD_INSTRUCTIONS_RECEIVED"]
        for s in shipped:
            assert WALLAPOP_TO_INTERNAL[s] == "ENVIADO"


class TestOrderToDict:
    def test_serialization(self):
        from src.extractors.orders import OrderData
        from src.parsers.lpn import LPNResult
        order = OrderData(
            request_id="req-1",
            wallapop_status="IN_TRANSIT",
            internal_status="ENVIADO",
            item_title="Test Item",
            lpn_result=LPNResult(lpns=["LPNWE001"], locations=["A/01"], num_products=1),
            shipping={"carrier": "InPost", "tracking_code": "TR1"},
        )
        d = WallapopOrdersExtractor._order_to_dict(order)
        assert d["request_id"] == "req-1"
        assert d["lpns"] == "LPNWE001"
        assert d["carrier"] == "InPost"
