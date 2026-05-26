"""
Shipping details extraction from Wallapop order data.

Handles carrier detection (InPost, Correos, Seur) from icon URLs and text,
deadline extraction from HTML content with bilingual date parsing,
tracking code extraction from nested API structures, and label URL
processing (converting wallapop:// deep links to web URLs).
"""
import logging
import re
from typing import Optional

from .dates import convert_spanish_date_to_timestamp

logger = logging.getLogger(__name__)

CARRIER_KEYWORDS = {
    "InPost": ["InPost", "inpost"],
    "Correos": ["Correos", "correos"],
    "Seur": ["Seur", "seur"],
}

DEADLINE_PATTERNS = [
    re.compile(r"entrégalo antes de que finalice el:?\s*<b>([^<]+)</b>", re.IGNORECASE | re.DOTALL),
    re.compile(r"antes del?\s*<b>([^<]+)</b>", re.IGNORECASE | re.DOTALL),
    re.compile(r"antes de(?:l)?\s*<strong>([^<]+)</strong>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<b>([^<]+\d{4})</b>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<strong>([^<]+\d{4})</strong>", re.IGNORECASE | re.DOTALL),
]

BOLD_PATTERN = re.compile(r"<b>([^<]+)</b>", re.IGNORECASE)
TRACKING_CODE_PATTERN = re.compile(r"<strong>([^<]+)</strong>")

DAY_NAMES = frozenset({
    "lunes", "martes", "miércoles", "miercoles", "jueves", "viernes",
    "sábado", "sabado", "domingo",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})
MONTH_NAMES = frozenset({
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
})


def detect_carrier(
    transaction_info: list[dict],
    shipping_status: dict,
) -> Optional[str]:
    """
    Detect shipping carrier from order data.

    Checks two sources in order:
    1. Icon URL in the first transaction_info entry (most reliable)
    2. Description text in shipping_status (fallback)
    """
    if transaction_info:
        first_info = transaction_info[0]
        icon_url = first_info.get("icon", {}).get("url", "")
        for carrier, keywords in CARRIER_KEYWORDS.items():
            if any(kw in icon_url for kw in keywords):
                return carrier

    if shipping_status:
        description = shipping_status.get("description", "")
        for carrier, keywords in CARRIER_KEYWORDS.items():
            if any(kw in description for kw in keywords):
                return carrier

    return None


def extract_deadline(shipping_status: dict) -> Optional[str]:
    """
    Extract the shipping deadline from HTML text in shipping_status.description.

    Wallapop embeds the deadline in bold tags within HTML content. The function
    tries multiple regex patterns and validates candidates by checking for
    both a date-related word (day/month name) and a digit.
    """
    if not shipping_status:
        return None

    description = shipping_status.get("description", "")
    if not description:
        return None

    for pattern in DEADLINE_PATTERNS:
        match = pattern.search(description)
        if match:
            candidate = match.group(1).strip()
            if _is_valid_date_candidate(candidate):
                return candidate

    for match in BOLD_PATTERN.finditer(description):
        candidate = match.group(1).strip()
        if _is_valid_date_candidate(candidate):
            return candidate

    return None


def _is_valid_date_candidate(text: str) -> bool:
    words = text.lower().replace(",", " ").split()
    has_date_word = any(w in DAY_NAMES or w in MONTH_NAMES for w in words)
    has_number = bool(re.search(r"\d", text))
    return has_date_word and has_number


def extract_tracking_code(transaction_info: list[dict]) -> Optional[str]:
    """
    Extract tracking/shipping code from transaction info.

    Checks two locations:
    1. Structured: action.payload.banner.tracking_code
    2. HTML fallback: <strong>CODE</strong> in description text
    """
    if not transaction_info:
        return None

    first_info = transaction_info[0]

    banner = first_info.get("action", {}).get("payload", {}).get("banner", {})
    if banner and banner.get("tracking_code"):
        return banner["tracking_code"]

    description = first_info.get("description", "")
    match = TRACKING_CODE_PATTERN.search(description)
    if match:
        return match.group(1).strip()

    return None


def extract_label_url(shipping_status: dict, carrier: Optional[str] = None) -> Optional[str]:
    """
    Extract and normalize shipping label URL.

    Wallapop uses deep link protocols (wallapop://) for label URLs.
    This converts them to regular web URLs.
    """
    if not shipping_status:
        return None

    actions = shipping_status.get("actions", [])
    for action in actions:
        title = action.get("title", "").lower()
        label_keywords = ["mostrar etiqueta", "view shipping label", "etiqueta", "label"]
        if any(kw in title for kw in label_keywords):
            link_url = action.get("action", {}).get("payload", {}).get("link_url")
            if link_url:
                return process_label_url(link_url, carrier)

    return None


def process_label_url(url: str, carrier: Optional[str] = None) -> str:
    """
    Convert wallapop:// deep link label URLs to web URLs.

    Handles two formats:
    - wallapop://trackinglabel?url=REAL_URL → strips prefix
    - wallapop://delivery/barcode?b=CODE → builds web URL
    """
    if url.startswith("wallapop://trackinglabel?url="):
        return url.replace("wallapop://trackinglabel?url=", "")
    elif url.startswith("wallapop://delivery/barcode?b="):
        barcode = url.split("b=")[1]
        return f"https://es.wallapop.com/app/delivery/tracking/barcode/{barcode}"
    return url


def extract_shipping_details(
    details_data: dict,
    request_id: str,
    order_status: Optional[str] = None,
) -> dict:
    """
    Extract all shipping-related details from order tracking data.

    Combines carrier detection, deadline extraction, tracking code,
    label URL, and buyer country into a single result dict.
    """
    result = {
        "carrier": None,
        "deadline": None,
        "deadline_timestamp": None,
        "tracking_code": None,
        "label_url": None,
        "instructions_url": f"https://es.wallapop.com/app/delivery/tracking/{request_id}/instructions/packaging",
        "buyer_country": None,
    }

    if not details_data:
        return result

    shipping_status = details_data.get("shipping_status", {}) or {}
    transaction_info = details_data.get("transaction_status_info", []) or []
    analytics = details_data.get("analytics", {}) or {}

    carrier = detect_carrier(transaction_info, shipping_status)
    result["carrier"] = carrier

    if order_status == "TRANSACTION_CREATED":
        deadline = extract_deadline(shipping_status)
        if deadline:
            result["deadline"] = deadline
            result["deadline_timestamp"] = convert_spanish_date_to_timestamp(deadline)

    result["tracking_code"] = extract_tracking_code(transaction_info)
    result["label_url"] = extract_label_url(shipping_status, carrier)
    result["buyer_country"] = analytics.get("buyer_country")

    return result
