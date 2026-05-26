"""
LPN extraction from item descriptions.

Wallapop listing descriptions embed LPN codes (inventory tracking IDs)
in free text. LPN formats vary:
  - Amazon FBA style: LPNWE324817902
  - Manual style: LPN1SIKA3, LPNKARCHER1
  - With location: LPNWE324817902 - DER/099

The regex captures both the LPN and an optional location suffix.
When a listing contains multiple LPNs (multi-item manual listings),
the total price is divided equally across LPNs.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

LPN_PATTERN = re.compile(
    r"\b(LPN[A-Za-z0-9]{3,}(?:\s*-\s*[A-Za-z0-9/\.]+)?)",
    re.IGNORECASE,
)

STRICT_LPN_PATTERN = re.compile(r"LPN[A-Z]{2}\d{6,12}", re.IGNORECASE)


@dataclass
class LPNResult:
    """Result of LPN extraction from a single item description."""
    lpns: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    prices: list[str] = field(default_factory=list)
    web_urls: list[str] = field(default_factory=list)
    num_products: int = 0

    @property
    def lpns_csv(self) -> str:
        return ",".join(self.lpns)

    @property
    def locations_csv(self) -> str:
        return ",".join(self.locations)

    @property
    def prices_csv(self) -> str:
        return ",".join(self.prices)


def extract_lpns_from_description(
    description: str,
    cost_amount: Optional[float] = None,
    sale_price: Optional[float] = None,
    web_url: str = "",
) -> LPNResult:
    """
    Extract LPN codes from a Wallapop item description.

    The regex is intentionally flexible to capture diverse LPN formats.
    Each LPN may optionally include a warehouse location separated by ' - '.

    When multiple LPNs are found, the order price is divided equally
    among them (production pattern for multi-item manual listings).
    """
    result = LPNResult()

    if not description:
        return result

    matches = LPN_PATTERN.findall(description)
    if not matches:
        if description:
            logger.debug("No LPNs found in description (%d chars)", len(description))
        return result

    seen: set[str] = set()

    for raw_match in matches:
        lpn_clean = raw_match.strip()
        lpn_simple = lpn_clean
        location = ""

        if " - " in lpn_clean:
            parts = lpn_clean.split(" - ", 1)
            lpn_simple = parts[0].strip()
            location = parts[1].strip()
        elif " " in lpn_clean:
            lpn_simple = lpn_clean.split(" ")[0].strip()

        lpn_simple = re.sub(r"\s+", "", lpn_simple)

        if lpn_simple and lpn_simple not in seen:
            seen.add(lpn_simple)
            result.lpns.append(lpn_simple)
            result.locations.append(location)
            result.web_urls.append(web_url)

    num_lpns = len(result.lpns)
    result.num_products = num_lpns

    if num_lpns > 0:
        effective_price = cost_amount if cost_amount is not None else sale_price
        if effective_price is not None:
            if num_lpns > 1:
                per_item = effective_price / num_lpns
                result.prices = [f"{per_item:.2f}"] * num_lpns
            else:
                result.prices = [f"{effective_price:.2f}"]

    if len(matches) != num_lpns:
        logger.info(
            "LPN parse: %d regex matches, %d unique LPNs (desc %d chars)",
            len(matches), num_lpns, len(description),
        )

    return result


def extract_single_lpn(description: str) -> Optional[str]:
    """
    Extract a single LPN from a description (for listings).

    Uses the strict pattern (LPNXX followed by 6-12 digits) since listing
    descriptions should contain exactly one LPN. Returns None if zero or
    multiple LPNs are found.
    """
    if not description:
        return None

    matches = STRICT_LPN_PATTERN.findall(description)

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        logger.warning("Multiple LPNs in description, discarding: %s", matches)
    return None
