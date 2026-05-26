"""
Wallapop listings extractor with anti-oscillation logic.

Wallapop re-publishes listings every ~3 days with a new product_id (item_hash).
Without special handling, this causes false "new listing" detections and
duplicate engagement metrics (views, favorites, conversations).

Anti-oscillation strategy:
- Each LPN is processed once per extraction run (first occurrence wins).
- previous_product_id tracks the last known ID to distinguish oscillations
  from genuine re-publications.
- Engagement metrics are accumulated across product_id changes, not reset.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .base_client import WallapopAPIClient

logger = logging.getLogger(__name__)

LPN_PATTERN = re.compile(r"LPN[A-Z]{2}\d{6,12}", re.IGNORECASE)


@dataclass
class ListingResult:
    """Parsed listing from the Wallapop published items API."""
    lpn: str
    product_id: str
    title: str = ""
    description: str = ""
    image_url: str = ""
    web_slug: str = ""
    category_id: Optional[int] = None
    sale_price: float = 0.0
    is_reserved: bool = False
    is_sold: bool = False
    is_banned: bool = False
    is_expired: bool = False
    conversations_count: int = 0
    favorites_count: int = 0
    views_count: int = 0
    modified_timestamp: Optional[int] = None
    published_timestamp: Optional[int] = None


@dataclass
class ListingDelta:
    """Tracks how a listing changed compared to its previous state."""
    lpn: str
    change_type: str  # "new", "updated", "oscillation", "product_id_changed", "unchanged"
    old_product_id: Optional[str] = None
    new_product_id: Optional[str] = None
    accumulated_views: int = 0
    accumulated_favorites: int = 0
    accumulated_conversations: int = 0


class WallapopListingsExtractor(WallapopAPIClient):
    """
    Extracts published listings from Wallapop's seller API.

    Pagination: Uses cursor-based pagination (X-Nextpage header → since param).
    Processes all pages until no more products are returned.

    Anti-oscillation:
    Wallapop rotates product IDs every ~3 days. This extractor tracks
    previous_product_id to detect when a "new" ID is actually just the old
    one coming back, preventing false metric accumulation.
    """

    PRODUCTS_ENDPOINT = "/api/v3/items/mine/published"
    PAGE_SIZE = 100
    MAX_PRODUCTS = 100_000

    def __init__(self, bearer_token: str, user_agents: list[str], **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self._stats = {
            "pages_fetched": 0,
            "products_found": 0,
            "with_lpn": 0,
            "without_lpn": 0,
        }
        self._seen_lpns: dict[str, str] = {}

    def extract(self, **kwargs) -> dict[str, Any]:
        """Extract all published listings."""
        logger.info("Starting listings extraction")

        next_token: Optional[str] = None
        page = 0
        results: list[ListingResult] = []

        while len(results) < self.MAX_PRODUCTS:
            products, next_token = self._fetch_page(next_token)
            page += 1
            self._stats["pages_fetched"] = page

            if not products:
                logger.info("No more products at page %d", page)
                break

            logger.info("Page %d: %d products", page, len(products))

            for product in products:
                parsed = self._parse_product(product)
                if not parsed:
                    self._stats["without_lpn"] += 1
                    continue

                if parsed.lpn in self._seen_lpns:
                    continue

                self._seen_lpns[parsed.lpn] = parsed.product_id
                results.append(parsed)
                self._stats["with_lpn"] += 1

            self._stats["products_found"] += len(products)

            if not next_token:
                logger.info("End of pagination (no next token)")
                break

            time.sleep(self._random_delay())

        logger.info(
            "Extraction complete: %d products, %d with LPN, %d without",
            self._stats["products_found"],
            self._stats["with_lpn"],
            self._stats["without_lpn"],
        )

        return {
            "listings": [self._result_to_dict(r) for r in results],
            **self._stats,
        }

    def detect_changes(
        self, current: list[ListingResult], previous_state: dict[str, dict],
    ) -> list[ListingDelta]:
        """
        Compare current extraction with previous state to detect changes.

        Args:
            current: Current extraction results.
            previous_state: Dict mapping LPN to previous listing state
                (must include 'product_id', 'previous_product_id', and metric fields).

        Returns:
            List of ListingDelta objects describing what changed.
        """
        deltas = []
        for listing in current:
            prev = previous_state.get(listing.lpn)
            if not prev:
                deltas.append(ListingDelta(lpn=listing.lpn, change_type="new", new_product_id=listing.product_id))
                continue

            old_pid = prev.get("product_id", "")
            if old_pid == listing.product_id:
                deltas.append(ListingDelta(lpn=listing.lpn, change_type="unchanged"))
                continue

            prev_prev_pid = prev.get("previous_product_id")
            if prev_prev_pid and listing.product_id == prev_prev_pid:
                deltas.append(ListingDelta(
                    lpn=listing.lpn,
                    change_type="oscillation",
                    old_product_id=old_pid,
                    new_product_id=listing.product_id,
                ))
            else:
                deltas.append(ListingDelta(
                    lpn=listing.lpn,
                    change_type="product_id_changed",
                    old_product_id=old_pid,
                    new_product_id=listing.product_id,
                    accumulated_views=prev.get("views_count", 0),
                    accumulated_favorites=prev.get("favorites_count", 0),
                    accumulated_conversations=prev.get("conversations_count", 0),
                ))

        return deltas

    def _fetch_page(self, since: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        url = f"{self.BASE_URL}{self.PRODUCTS_ENDPOINT}"
        headers = self._build_headers()
        params: dict = {}
        if since:
            params["since"] = since
        else:
            params["page_size"] = self.PAGE_SIZE

        import requests as req
        try:
            response = req.get(url, headers=headers, params=params, timeout=self.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.error("HTTP %d fetching listings page", response.status_code)
                return [], None

            data = response.json()
            products = data if isinstance(data, list) else []

            next_token = None
            next_header = response.headers.get("X-Nextpage", "")
            if next_header and "since=" in next_header:
                since_part = next_header.split("since=")[1]
                next_token = since_part.split(":")[0] if ":" in since_part else since_part

            return products, next_token
        except Exception as e:
            logger.error("Error fetching listings page: %s", e)
            return [], None

    def _parse_product(self, product: dict) -> Optional[ListingResult]:
        try:
            content = product.get("content", {})
            flags = content.get("flags", {})
            image = content.get("image", {})
            description = content.get("description", "")

            match = LPN_PATTERN.search(description or "")
            if not match:
                return None

            return ListingResult(
                lpn=match.group(),
                product_id=product.get("id", ""),
                title=content.get("title", ""),
                description=description,
                image_url=image.get("original", ""),
                web_slug=content.get("web_slug", ""),
                category_id=content.get("category_id"),
                sale_price=content.get("sale_price", 0),
                is_reserved=flags.get("reserved", False),
                is_sold=flags.get("sold", False),
                is_banned=flags.get("banned", False),
                is_expired=flags.get("expired", False),
                conversations_count=content.get("conversations", 0),
                favorites_count=content.get("favorites", 0),
                views_count=content.get("views", 0),
                modified_timestamp=content.get("modified_date"),
                published_timestamp=content.get("publish_date"),
            )
        except Exception as e:
            logger.error("Error parsing product: %s", e)
            return None

    @staticmethod
    def _result_to_dict(r: ListingResult) -> dict:
        return {
            "lpn": r.lpn,
            "product_id": r.product_id,
            "title": r.title,
            "sale_price": r.sale_price,
            "is_reserved": r.is_reserved,
            "is_sold": r.is_sold,
            "conversations": r.conversations_count,
            "favorites": r.favorites_count,
            "views": r.views_count,
        }
