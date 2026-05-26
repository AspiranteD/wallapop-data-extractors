"""
Wallapop orders extractor.

Extracts active orders (deliveries) from the Wallapop seller API with a
multi-step enrichment pipeline:

1. GET /bff/delivery/deliveries/ongoing/as_seller → active order list
2. GET /bff/delivery/transaction-tracking?requestId=X → tracking + shipping
3. GET /api/v3/items/{hash}/vertical → item details (LPN from description)
4. GET /bff/delivery/transaction-tracking-details?requestId=X → bundle items

Key production patterns:
- Bundle detection: wallapop://i/ deep links → individual item hashes
- LPN extraction: flexible regex capturing Amazon FBA and manual formats
- Shipping parsing: carrier from icon URLs, deadline from HTML, label URL conversion
- Price splitting: total price ÷ number of LPNs for multi-item listings
- Bearer auto-renewal on 401 with retries
- Idempotent updates: only update empty fields on existing orders
"""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .base_client import WallapopAPIClient
from ..parsers.status import WALLAPOP_TO_INTERNAL, SHIPPED_STATES, RETURN_STATES
from ..parsers.lpn import extract_lpns_from_description, LPNResult
from ..parsers.shipping import extract_shipping_details

logger = logging.getLogger(__name__)

PERMANENT_FAIL_ATTEMPTS = 99


@dataclass
class OrderData:
    """Parsed order with all enrichment data."""
    request_id: str
    wallapop_status: str
    internal_status: str
    item_title: Optional[str] = None
    item_hash: Optional[str] = None
    buyer_name: Optional[str] = None
    cost_amount: Optional[float] = None
    is_bundle: bool = False
    bundle_size: int = 1
    lpn_result: Optional[LPNResult] = None
    shipping: Optional[dict] = None
    created_at: Optional[datetime] = None


@dataclass
class ExtractionStats:
    """Tracks extraction metrics."""
    orders_found: int = 0
    orders_processed: int = 0
    new_orders: int = 0
    updated_orders: int = 0
    bundles_detected: int = 0
    lpns_extracted: int = 0
    bearer_renewals: int = 0
    errors: int = 0


class WallapopOrdersExtractor(WallapopAPIClient):
    """
    Extracts and enriches order data from Wallapop's seller API.

    The enrichment pipeline fetches order list, then for each order:
    tracking details, item details (for LPN extraction), and bundle
    details (for multi-item orders).
    """

    DELIVERIES_ENDPOINT = "/bff/delivery/deliveries/ongoing/as_seller"
    TRACKING_ENDPOINT = "/bff/delivery/transaction-tracking"
    ITEM_VERTICAL_ENDPOINT = "/api/v3/items/{item_hash}/vertical"
    BUNDLE_DETAILS_ENDPOINT = "/bff/delivery/transaction-tracking-details"

    DELAY_BETWEEN_REQUESTS = 0.1
    MAX_LPN_RETRIES = 3

    def __init__(self, bearer_token: str, user_agents: list[str], **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self.stats = ExtractionStats()

    def extract(self, **kwargs) -> dict[str, Any]:
        """Run the full extraction pipeline."""
        logger.info("Starting Wallapop orders extraction")

        orders_raw = self._fetch_active_orders()
        if orders_raw is None:
            return {"error": self.last_error, "stats": self.stats.__dict__}

        self.stats.orders_found = len(orders_raw)
        results: list[OrderData] = []

        for idx, order_raw in enumerate(orders_raw, 1):
            logger.info("[%d/%d] Processing order", idx, len(orders_raw))
            try:
                order_data = self._process_single_order(order_raw)
                if order_data:
                    results.append(order_data)
                    self.stats.orders_processed += 1
            except Exception as e:
                logger.error("Error processing order: %s", e)
                self.stats.errors += 1
            time.sleep(self.DELAY_BETWEEN_REQUESTS)

        logger.info(
            "Extraction complete: %d found, %d processed, %d bundles, %d LPNs",
            self.stats.orders_found, self.stats.orders_processed,
            self.stats.bundles_detected, self.stats.lpns_extracted,
        )
        return {
            "orders": [self._order_to_dict(o) for o in results],
            "stats": self.stats.__dict__,
        }

    def _fetch_active_orders(self) -> Optional[list[dict]]:
        url = f"{self.BASE_URL}{self.DELIVERIES_ENDPOINT}"
        headers = self._build_headers()
        data = self._request(url, headers)
        if data is None:
            return None
        orders = data.get("ongoing_deliveries", [])
        logger.info("Found %d active orders", len(orders))
        return orders

    def _process_single_order(self, order_raw: dict) -> Optional[OrderData]:
        request_id = order_raw.get("request_id")
        if not request_id:
            return None

        raw_status = order_raw.get("status", "UNKNOWN")
        internal_status = WALLAPOP_TO_INTERNAL.get(raw_status, raw_status)

        item_data = order_raw.get("item", {}) or {}
        cost_amount = item_data.get("cost", {}).get("amount") if isinstance(item_data.get("cost"), dict) else None

        order = OrderData(
            request_id=request_id,
            wallapop_status=raw_status,
            internal_status=internal_status,
            item_title=item_data.get("title"),
            item_hash=item_data.get("hash"),
            cost_amount=cost_amount,
        )

        details = self._fetch_tracking_details(request_id)
        if details:
            order.shipping = extract_shipping_details(details, request_id, raw_status)

        bundle_info = order_raw.get("bundle", {}) or {}
        bundle_size = bundle_info.get("size", 1)

        if bundle_size > 1:
            order.is_bundle = True
            order.bundle_size = bundle_size
            self.stats.bundles_detected += 1
            order.lpn_result = self._extract_bundle_lpns(request_id, cost_amount)
        else:
            if order.item_hash:
                order.lpn_result = self._extract_single_item_lpns(
                    order.item_hash, cost_amount,
                )

        if order.lpn_result and order.lpn_result.num_products > 0:
            self.stats.lpns_extracted += order.lpn_result.num_products

        return order

    def _fetch_tracking_details(self, request_id: str) -> Optional[dict]:
        """GET tracking details using query params (not URL path)."""
        url = f"{self.BASE_URL}{self.TRACKING_ENDPOINT}"
        headers = self._build_headers(endpoint_type="details")
        params = {"requestId": request_id}
        data = self._request(url, headers, params)
        if data:
            time.sleep(self.DELAY_BETWEEN_REQUESTS)
        return data

    def _fetch_item_vertical(self, item_hash: str) -> Optional[dict]:
        url = f"{self.BASE_URL}{self.ITEM_VERTICAL_ENDPOINT.format(item_hash=item_hash)}"
        headers = self._build_headers(endpoint_type="vertical")
        data = self._request(url, headers)
        if data:
            time.sleep(self.DELAY_BETWEEN_REQUESTS)
        return data

    def _fetch_bundle_details(self, request_id: str) -> Optional[dict]:
        """GET bundle details using query params."""
        url = f"{self.BASE_URL}{self.BUNDLE_DETAILS_ENDPOINT}"
        headers = self._build_headers(endpoint_type="details")
        params = {"requestId": request_id}
        data = self._request(url, headers, params)
        if data:
            time.sleep(self.DELAY_BETWEEN_REQUESTS)
        return data

    def _extract_bundle_lpns(
        self, request_id: str, cost_amount: Optional[float],
    ) -> LPNResult:
        """
        Extract LPNs from all items in a bundle.

        Production pattern: bundle details contain a details_info array where
        each entry may have a wallapop://i/{hash} deep link. We extract hashes,
        fetch each item's vertical data, and parse LPNs from descriptions.
        """
        combined = LPNResult()

        bundle_data = self._fetch_bundle_details(request_id)
        if not bundle_data:
            return combined

        item_hashes = self._extract_hashes_from_bundle(bundle_data)
        if not item_hashes:
            return combined

        all_lpns = []
        all_locations = []
        all_urls = []

        for item_hash in item_hashes:
            item_data = self._fetch_item_vertical(item_hash)
            if not item_data:
                logger.warning("Bundle: could not fetch item %s", item_hash)
                continue

            content = item_data.get("content", {})
            description = content.get("description", "")
            web_url = content.get("web_url", "")

            lpn_result = extract_lpns_from_description(description, web_url=web_url)

            for i, lpn in enumerate(lpn_result.lpns):
                if lpn not in all_lpns:
                    all_lpns.append(lpn)
                    loc = lpn_result.locations[i] if i < len(lpn_result.locations) else ""
                    all_locations.append(loc)
                    all_urls.append(web_url)

        if all_lpns:
            combined.lpns = all_lpns
            combined.locations = all_locations
            combined.web_urls = all_urls
            combined.num_products = len(all_lpns)

            if cost_amount is not None and len(all_lpns) > 1:
                per_item = cost_amount / len(all_lpns)
                combined.prices = [f"{per_item:.2f}"] * len(all_lpns)
            elif cost_amount is not None:
                combined.prices = [f"{cost_amount:.2f}"]

        return combined

    @staticmethod
    def _extract_hashes_from_bundle(bundle_data: dict) -> list[str]:
        """
        Extract item hashes from bundle details_info via wallapop://i/ links.

        The details_info array contains a mix of product entries and
        non-product entries (totals, shipping info). Only entries with
        wallapop://i/ action links are actual products.
        """
        hashes = []
        details_info = bundle_data.get("details_info", [])

        for detail in details_info:
            action = detail.get("action", {})
            payload = action.get("payload", {})
            link_url = payload.get("link_url", "")

            if link_url.startswith("wallapop://i/"):
                item_hash = link_url.replace("wallapop://i/", "").strip()
                if item_hash and item_hash not in hashes:
                    hashes.append(item_hash)

        logger.info(
            "Bundle: %d item hashes extracted from %d details_info entries",
            len(hashes), len(details_info),
        )
        return hashes

    def _extract_single_item_lpns(
        self, item_hash: str, cost_amount: Optional[float],
    ) -> LPNResult:
        """Extract LPNs from a single item's description."""
        item_data = self._fetch_item_vertical(item_hash)
        if not item_data:
            return LPNResult()

        content = item_data.get("content", {})
        description = content.get("description", "")
        sale_price = content.get("sale_price")
        web_url = content.get("web_url", "")

        return extract_lpns_from_description(
            description,
            cost_amount=cost_amount,
            sale_price=sale_price,
            web_url=web_url,
        )

    @staticmethod
    def _order_to_dict(order: OrderData) -> dict:
        return {
            "request_id": order.request_id,
            "wallapop_status": order.wallapop_status,
            "internal_status": order.internal_status,
            "item_title": order.item_title,
            "item_hash": order.item_hash,
            "cost_amount": order.cost_amount,
            "is_bundle": order.is_bundle,
            "bundle_size": order.bundle_size,
            "lpns": order.lpn_result.lpns_csv if order.lpn_result else "",
            "locations": order.lpn_result.locations_csv if order.lpn_result else "",
            "prices": order.lpn_result.prices_csv if order.lpn_result else "",
            "num_products": order.lpn_result.num_products if order.lpn_result else 0,
            "carrier": order.shipping.get("carrier") if order.shipping else None,
            "tracking_code": order.shipping.get("tracking_code") if order.shipping else None,
            "deadline": order.shipping.get("deadline") if order.shipping else None,
        }
