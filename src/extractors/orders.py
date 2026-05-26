"""
Wallapop orders extractor.

Extracts active orders (deliveries) from the Wallapop seller API, including
transaction details, tracking information, and item metadata.

Key features:
- Status mapping from Wallapop's 18+ internal states to 9 unified states
- Multi-step data enrichment (deliveries → tracking → item details)
- Bundle order support (multiple items per transaction)
- LPN extraction from item descriptions via regex
"""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .base_client import WallapopAPIClient

logger = logging.getLogger(__name__)

WALLAPOP_TO_INTERNAL = {
    "TRANSACTION_CREATED": "PENDING_SHIPMENT",
    "REQUEST_CREATED": "PENDING_SHIPMENT",
    "PENDING_TO_MEET": "PENDING_SHIPMENT",
    "DEPOSITED_AT_PUDO": "SHIPPED",
    "AVAILABLE_FOR_THE_RECIPIENT_CARRIER_OFFICE": "SHIPPED",
    "TRANSACTION_DEFAULT": "SHIPPED",
    "DELIVERED_TO_CARRIER": "SHIPPED",
    "IN_TRANSIT": "SHIPPED",
    "AVAILABLE_FOR_THE_RECIPIENT_HOME_PICKUP": "SHIPPED",
    "ON_HOLD_AT_CARRIER": "SHIPPED",
    "ON_HOLD_INSTRUCTIONS_RECEIVED": "SHIPPED",
    "DELIVERED": "DELIVERED",
    "DISPUTE_OPEN": "INCIDENT",
    "DISPUTE_ESCALATED": "INCIDENT",
    "RETURN_INCIDENT": "INCIDENT",
    "RETURN_IN_PROGRESS": "RETURNING",
    "DELIVERY_RETURNING_TO_SENDER": "RETURNING",
    "CANCELLED": "CANCELLED",
}

SHIPPED_STATES = {"SHIPPED", "DELIVERED", "COMPLETED"}
RETURN_STATES = {"RETURNING", "INCIDENT"}

LPN_PATTERN = re.compile(r"LPN[A-Z]{2}\d{6,12}", re.IGNORECASE)


@dataclass
class OrderResult:
    """Result of processing a single order."""
    request_id: str
    status: str
    internal_status: str
    buyer_name: Optional[str] = None
    item_title: Optional[str] = None
    item_hash: Optional[str] = None
    lpn: Optional[str] = None
    price_cents: Optional[int] = None
    shipping_carrier: Optional[str] = None
    tracking_code: Optional[str] = None
    created_at: Optional[datetime] = None
    is_bundle: bool = False
    bundle_items: list = field(default_factory=list)


class WallapopOrdersExtractor(WallapopAPIClient):
    """
    Extracts order data from Wallapop's seller API.

    Enrichment pipeline per order:
    1. GET /bff/delivery/deliveries/ongoing/as_seller → list of active orders
    2. GET /bff/delivery/transaction-tracking/{request_id} → tracking + buyer info
    3. GET /api/v3/items/{item_hash}/vertical → item details (LPN, description)
    4. GET /bff/delivery/transaction-tracking-details/{request_id} → bundle items
    """

    DELIVERIES_ENDPOINT = "/bff/delivery/deliveries/ongoing/as_seller"
    TRACKING_ENDPOINT = "/bff/delivery/transaction-tracking"
    ITEM_VERTICAL_ENDPOINT = "/api/v3/items/{item_hash}/vertical"
    BUNDLE_ENDPOINT = "/bff/delivery/transaction-tracking-details"

    def __init__(self, bearer_token: str, user_agents: list[str], **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self._stats = {
            "orders_found": 0,
            "orders_enriched": 0,
            "bundles_detected": 0,
            "lpns_extracted": 0,
            "errors": 0,
        }

    def extract(self, **kwargs) -> dict[str, Any]:
        """Extract all active orders with full enrichment."""
        logger.info("Starting order extraction")

        orders_data = self._fetch_active_orders()
        if orders_data is None:
            return {"error": self.last_error, **self._stats}

        self._stats["orders_found"] = len(orders_data)
        results: list[OrderResult] = []

        for i, order in enumerate(orders_data, 1):
            logger.info("Processing order %d/%d", i, len(orders_data))
            result = self._process_order(order)
            if result:
                results.append(result)
                self._stats["orders_enriched"] += 1
            time.sleep(self._random_delay())

        logger.info(
            "Extraction complete: %d orders, %d enriched, %d bundles, %d LPNs",
            self._stats["orders_found"],
            self._stats["orders_enriched"],
            self._stats["bundles_detected"],
            self._stats["lpns_extracted"],
        )

        return {
            "orders": [self._result_to_dict(r) for r in results],
            **self._stats,
        }

    def _fetch_active_orders(self) -> Optional[list[dict]]:
        url = f"{self.BASE_URL}{self.DELIVERIES_ENDPOINT}"
        headers = self._build_headers()
        data = self._request(url, headers)
        if data is None:
            return None
        return data.get("ongoing_deliveries", [])

    def _process_order(self, order: dict) -> Optional[OrderResult]:
        request_id = order.get("request_id", "")
        delivery = order.get("delivery", {}) or {}
        item = order.get("item", {}) or {}

        raw_status = delivery.get("status", "UNKNOWN")
        internal_status = WALLAPOP_TO_INTERNAL.get(raw_status, "UNKNOWN")

        result = OrderResult(
            request_id=request_id,
            status=raw_status,
            internal_status=internal_status,
            item_title=item.get("title"),
            item_hash=item.get("hash"),
            price_cents=item.get("price", {}).get("amount") if isinstance(item.get("price"), dict) else None,
        )

        tracking = self._fetch_tracking(request_id)
        if tracking:
            buyer = tracking.get("buyer", {}) or {}
            result.buyer_name = buyer.get("name")
            shipping = tracking.get("shipping_info", {}) or {}
            result.shipping_carrier = shipping.get("carrier")
            result.tracking_code = shipping.get("tracking_code")
            created_str = tracking.get("created_at")
            if created_str:
                try:
                    result.created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

        if result.item_hash:
            item_details = self._fetch_item_details(result.item_hash)
            if item_details:
                description = item_details.get("description", "")
                lpn_match = LPN_PATTERN.search(description or "")
                if lpn_match:
                    result.lpn = lpn_match.group()
                    self._stats["lpns_extracted"] += 1

        bundle_items = self._fetch_bundle_details(request_id)
        if bundle_items and len(bundle_items) > 1:
            result.is_bundle = True
            result.bundle_items = bundle_items
            self._stats["bundles_detected"] += 1

        return result

    def _fetch_tracking(self, request_id: str) -> Optional[dict]:
        url = f"{self.BASE_URL}{self.TRACKING_ENDPOINT}/{request_id}"
        headers = self._build_headers(endpoint_type="details")
        return self._request(url, headers)

    def _fetch_item_details(self, item_hash: str) -> Optional[dict]:
        url = f"{self.BASE_URL}{self.ITEM_VERTICAL_ENDPOINT.format(item_hash=item_hash)}"
        headers = self._build_headers(endpoint_type="vertical")
        return self._request(url, headers)

    def _fetch_bundle_details(self, request_id: str) -> Optional[list[dict]]:
        url = f"{self.BASE_URL}{self.BUNDLE_ENDPOINT}/{request_id}"
        headers = self._build_headers(endpoint_type="details")
        data = self._request(url, headers)
        if not data:
            return None
        return data.get("items", [])

    @staticmethod
    def _result_to_dict(r: OrderResult) -> dict:
        return {
            "request_id": r.request_id,
            "status": r.status,
            "internal_status": r.internal_status,
            "buyer_name": r.buyer_name,
            "item_title": r.item_title,
            "item_hash": r.item_hash,
            "lpn": r.lpn,
            "price_cents": r.price_cents,
            "shipping_carrier": r.shipping_carrier,
            "tracking_code": r.tracking_code,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "is_bundle": r.is_bundle,
            "bundle_item_count": len(r.bundle_items),
        }
