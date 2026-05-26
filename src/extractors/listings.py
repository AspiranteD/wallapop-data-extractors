"""
Wallapop listings extractor with anti-oscillation and stats accumulation.

IMPORTANT: Wallapop product_ids (item_hash) change with every resubmission
(~every 3 days). The LPN is the real product identifier.

The API can return the same LPN with different product_ids across pages
within a single extraction run (old and new coexist).

Anti-oscillation strategy:
- Each LPN is processed ONCE per run (first occurrence wins).
- previous_product_id is stored to distinguish oscillations from real changes.
  If the "new" ID matches previous_product_id, it's oscillation → swap without
  accumulating stats. Only truly new IDs trigger stat accumulation.

Stats accumulation:
- When product_id changes (real resubmission), current conversations/favorites/views
  counters are added to *_accumulated fields before reset. This preserves
  lifetime engagement metrics across Wallapop's forced resubmissions.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .base_client import WallapopAPIClient
from ..parsers.lpn import extract_single_lpn

logger = logging.getLogger(__name__)


@dataclass
class ListingData:
    """Parsed listing from Wallapop API."""
    lpn: str
    account_id: int
    product_id: str
    title: str = ""
    description: str = ""
    image_url: str = ""
    web_slug: str = ""
    category_id: Optional[int] = None
    sale_price: float = 0.0
    is_reserved: bool = False
    is_sold: bool = False
    is_pending: bool = False
    is_banned: bool = False
    is_expired: bool = False
    is_on_hold: bool = False
    conversations_count: int = 0
    favorites_count: int = 0
    views_count: int = 0
    modified_timestamp: Optional[int] = None
    published_timestamp: Optional[int] = None


@dataclass
class StoredListing:
    """Represents a listing already in the database for comparison."""
    lpn: str
    product_id: str
    previous_product_id: Optional[str] = None
    conversations_count: int = 0
    favorites_count: int = 0
    views_count: int = 0
    conversations_accumulated: int = 0
    favorites_accumulated: int = 0
    views_accumulated: int = 0
    is_reserved: bool = False
    is_sold: bool = False
    is_pending: bool = False
    is_banned: bool = False
    is_expired: bool = False
    is_on_hold: bool = False
    sale_price: float = 0.0


@dataclass
class ListingUpdate:
    """Describes what changed for a listing."""
    lpn: str
    action: str  # 'new', 'updated', 'product_id_changed', 'unchanged'
    listing_data: ListingData
    new_conversations_accumulated: int = 0
    new_favorites_accumulated: int = 0
    new_views_accumulated: int = 0
    new_previous_product_id: Optional[str] = None


class WallapopListingsExtractor(WallapopAPIClient):
    """
    Extracts listings from Wallapop with anti-oscillation and engagement
    stat accumulation across resubmissions.
    """

    PRODUCTS_ENDPOINT = "/api/v3/items/mine/published"
    PAGE_SIZE = 100
    DELAY_BETWEEN_REQUESTS = 0.1
    MAX_PRODUCTS = 100_000

    def __init__(self, bearer_token: str, user_agents: list[str], account_id: int = 0, **kwargs):
        super().__init__(bearer_token, user_agents, **kwargs)
        self._account_id = account_id
        self._stats = {
            "products_extracted": 0,
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "product_id_changes": 0,
            "no_lpn": 0,
            "errors": 0,
        }

    def extract(
        self,
        existing_listings: Optional[dict[str, StoredListing]] = None,
        on_update: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Extract all listings for this account.

        Args:
            existing_listings: Dict mapping LPN → StoredListing for comparison.
                If None, all listings are treated as new.
            on_update: Optional callback(ListingUpdate) for persistence.
        """
        if existing_listings is None:
            existing_listings = {}

        lpn_seen_this_run: dict[str, str] = {}
        updates: list[ListingUpdate] = []

        next_token: Optional[str] = None
        page = 1

        while self._stats["products_extracted"] < self.MAX_PRODUCTS:
            products, next_token = self._fetch_products_page(next_token)

            if not products:
                break

            logger.info("Page %d: %d products", page, len(products))

            for product in products:
                self._stats["products_extracted"] += 1

                parsed = self._parse_product(product)
                if not parsed:
                    self._stats["no_lpn"] += 1
                    continue

                update = self._process_listing(
                    parsed, existing_listings, lpn_seen_this_run,
                )
                if update:
                    updates.append(update)
                    if on_update:
                        on_update(update)

            if not next_token:
                logger.info("End of pagination (no next token)")
                break

            page += 1
            time.sleep(self.DELAY_BETWEEN_REQUESTS)

        logger.info(
            "Listings extraction: %d extracted, %d new, %d updated, "
            "%d unchanged, %d product_id changes, %d no LPN",
            self._stats["products_extracted"], self._stats["new"],
            self._stats["updated"], self._stats["unchanged"],
            self._stats["product_id_changes"], self._stats["no_lpn"],
        )

        return {"updates": updates, "stats": self._stats}

    def _fetch_products_page(
        self, since: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """
        Fetch a page of products.

        Pagination uses the X-Nextpage response header containing
        a 'since=' token, not standard offset pagination.
        """
        import requests as req

        headers = self._build_headers()
        params: dict[str, Any] = {}
        if since:
            params["since"] = since
        else:
            params["page_size"] = self.PAGE_SIZE

        url = f"{self.BASE_URL}{self.PRODUCTS_ENDPOINT}"
        try:
            response = req.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                products = data if isinstance(data, list) else []

                next_token = None
                next_header = response.headers.get("X-Nextpage", "")
                if next_header and "since=" in next_header:
                    since_part = next_header.split("since=")[1]
                    next_token = since_part.split(":")[0] if ":" in since_part else since_part

                return products, next_token

            elif response.status_code == 401:
                if self._refresh_callback:
                    new_token = self._refresh_callback()
                    if new_token:
                        self._bearer = new_token
                        return [], None
                return [], None

            else:
                logger.error("HTTP %d fetching products", response.status_code)
                return [], None

        except Exception as e:
            logger.error("Error fetching products: %s", e)
            return [], None

    def _parse_product(self, product: dict) -> Optional[ListingData]:
        content = product.get("content", {})
        flags = content.get("flags", {})
        image = content.get("image", {})

        description = content.get("description", "")
        lpn = extract_single_lpn(description)
        if not lpn:
            return None

        return ListingData(
            lpn=lpn,
            account_id=self._account_id,
            product_id=product.get("id", ""),
            title=content.get("title", ""),
            description=description,
            image_url=image.get("original", ""),
            web_slug=content.get("web_slug", ""),
            category_id=content.get("category_id"),
            sale_price=content.get("sale_price", 0),
            is_reserved=flags.get("reserved", False),
            is_sold=flags.get("sold", False),
            is_pending=flags.get("pending", False),
            is_banned=flags.get("banned", False),
            is_expired=flags.get("expired", False),
            is_on_hold=flags.get("onhold", False),
            conversations_count=content.get("conversations", 0),
            favorites_count=content.get("favorites", 0),
            views_count=content.get("views", 0),
            modified_timestamp=content.get("modified_date"),
            published_timestamp=content.get("publish_date"),
        )

    def _process_listing(
        self,
        parsed: ListingData,
        existing: dict[str, StoredListing],
        seen_this_run: dict[str, str],
    ) -> Optional[ListingUpdate]:
        """
        Process a single listing with anti-oscillation logic.

        Returns a ListingUpdate describing what action to take, or None
        if this LPN was already processed in this run (dedup).
        """
        lpn = parsed.lpn
        new_pid = parsed.product_id

        if lpn in seen_this_run:
            return None

        seen_this_run[lpn] = new_pid

        if lpn not in existing:
            self._stats["new"] += 1
            return ListingUpdate(lpn=lpn, action="new", listing_data=parsed)

        stored = existing[lpn]
        old_pid = stored.product_id or ""

        if old_pid != new_pid:
            is_oscillation = (
                stored.previous_product_id is not None
                and new_pid == stored.previous_product_id
            )

            if is_oscillation:
                self._stats["updated"] += 1
                return ListingUpdate(
                    lpn=lpn,
                    action="updated",
                    listing_data=parsed,
                    new_previous_product_id=old_pid,
                )

            new_conv_acc = (stored.conversations_accumulated or 0) + (stored.conversations_count or 0)
            new_fav_acc = (stored.favorites_accumulated or 0) + (stored.favorites_count or 0)
            new_views_acc = (stored.views_accumulated or 0) + (stored.views_count or 0)

            self._stats["product_id_changes"] += 1
            self._stats["updated"] += 1

            logger.info(
                "Product ID changed for %s: %s → %s "
                "(accumulated: C=%d, F=%d, V=%d)",
                lpn, old_pid[:12], new_pid[:12],
                new_conv_acc, new_fav_acc, new_views_acc,
            )

            return ListingUpdate(
                lpn=lpn,
                action="product_id_changed",
                listing_data=parsed,
                new_conversations_accumulated=new_conv_acc,
                new_favorites_accumulated=new_fav_acc,
                new_views_accumulated=new_views_acc,
                new_previous_product_id=old_pid,
            )

        stats_changed = (
            stored.conversations_count != parsed.conversations_count
            or stored.favorites_count != parsed.favorites_count
            or stored.views_count != parsed.views_count
        )
        flags_changed = (
            stored.is_reserved != parsed.is_reserved
            or stored.is_sold != parsed.is_sold
            or stored.is_pending != parsed.is_pending
            or stored.is_banned != parsed.is_banned
            or stored.is_expired != parsed.is_expired
            or stored.is_on_hold != parsed.is_on_hold
            or stored.sale_price != parsed.sale_price
        )

        if stats_changed or flags_changed:
            self._stats["updated"] += 1
            return ListingUpdate(lpn=lpn, action="updated", listing_data=parsed)

        self._stats["unchanged"] += 1
        return None
