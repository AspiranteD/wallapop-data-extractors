# Wallapop Data Extractors
> **Portfolio context:** Extracted from founder-led production systems — multi-marketplace inventory, orders, and warehouse execution. **[Full portfolio](https://github.com/AspiranteD)** · [aspiranted.github.io](https://aspiranted.github.io)

Production-grade data extraction framework for the Wallapop marketplace API. Extracts orders, conversations, and listings across multiple seller accounts with anti-detection, anti-oscillation, and robust error handling.

## Architecture

```
src/
├── extractors/
│   ├── base_client.py         # Abstract API client: anti-detection, retries, rate limiting
│   ├── orders.py              # Orders extractor with bundle/LPN/shipping enrichment
│   ├── chats.py               # Conversations extractor with change detection
│   └── listings.py            # Listings extractor with anti-oscillation
├── parsers/
│   ├── lpn.py                 # LPN regex extraction with location parsing
│   ├── shipping.py            # Carrier detection, deadline parsing, label URL conversion
│   ├── dates.py               # Spanish-English bilingual date conversion
│   └── status.py              # Wallapop → internal status mapping (18 states → 9)
```

## Key Technical Features

### Anti-Detection (base_client.py)
The API client randomizes every aspect of requests to avoid bot detection:
- **Device ID rotation**: Random `android-{16 hex chars}` per request
- **User agent rotation**: Pool of real browser strings
- **App version variation**: Cycles through 5 real Wallapop app versions
- **Accept-Language variation**: 4 locale combinations
- **Referer injection**: Random Wallapop URLs added 70% of the time
- **Cache-Control headers**: Random cache directives 30% of the time
- **Biased delay distribution**: 70% short delays, 30% longer (mimics human timing)
- **Endpoint-specific headers**: Different profiles for listing, tracking, and vertical endpoints

### Orders Enrichment Pipeline (orders.py)
Four-stage data enrichment per order:
1. **Deliveries list** → active orders
2. **Transaction tracking** → shipping details (carrier, tracking code, deadline)
3. **Item vertical** → product details (description → LPN extraction)
4. **Bundle details** → individual items via `wallapop://i/` deep links

**LPN Extraction**: Flexible regex `LPN[A-Za-z0-9]{3,}` captures:
- Amazon FBA format: `LPNWE324817902`
- Manual format: `LPN1SIKA3`, `LPNKARCHER1`
- With location: `LPNWE324817902 - DER/099` (splits LPN from warehouse location)

**Bundle Handling**: Multi-item orders embed item hashes as `wallapop://i/{hash}` deep links in `details_info`. Each hash is fetched individually to extract per-item LPNs.

**Price Splitting**: Total order price ÷ number of LPNs for multi-item listings.

### Shipping Details Parsing (shipping.py)
- **Carrier detection**: Identifies InPost/Correos/Seur from icon URLs (primary) and description text (fallback)
- **Deadline extraction**: Multiple regex patterns parse dates from HTML `<b>` and `<strong>` tags, validated for date-like content
- **Tracking code**: Structured extraction from `action.payload.banner.tracking_code` with HTML `<strong>` fallback
- **Label URL conversion**: `wallapop://trackinglabel?url=X` → `X`, `wallapop://delivery/barcode?b=X` → web URL

### Bilingual Date Parsing (dates.py)
Shipping deadlines arrive as localized Spanish strings (e.g., "Viernes, 23 Mayo 2025"). The parser translates Spanish day/month names to English, then uses standard `strptime` parsing.

### Status Mapping (status.py)
18 Wallapop API states normalized to 9 internal states (POR_ENVIAR, ENVIADO, ENTREGADO, COMPLETADO, EN_DEVOLUCION, DEVUELTO, CANCELADO, REEMBOLSADO, INCIDENCIA). State sets define business rules (SHIPPED_STATES, RETURN_STATES, ITEMS_LEFT_WAREHOUSE).

### Anti-Oscillation (listings.py)
Wallapop forces listing resubmission every ~3 days, changing the `product_id`. The API can return the same LPN with different product_ids across pages within a single run.

**Strategy**:
1. **Per-run dedup**: Each LPN processed once per extraction (first occurrence wins)
2. **Oscillation detection**: If "new" product_id matches `previous_product_id`, it's oscillation → swap IDs without accumulating stats
3. **Real change**: Truly new product_ids trigger stat accumulation (conversations, favorites, views added to `*_accumulated` fields)

### Conversation Change Detection (chats.py)
- **Token-based pagination**: Uses `next_from` tokens, not offset-based
- **COMPARABLE_FIELDS**: Only updates conversations where monitored fields changed (total_messages, unread_messages, is_sold, last_message_timestamp, item_status, item_price)
- **Cross-account merge**: Same conversation may appear under different accounts — timestamps are merged keeping the most recent

## Usage

### Orders
```python
from src.extractors import WallapopOrdersExtractor

extractor = WallapopOrdersExtractor(
    bearer_token="your-token",
    user_agents=["Mozilla/5.0 ..."],
    token_refresh_callback=lambda: get_new_token(),
)
result = extractor.extract()
for order in result["orders"]:
    print(f"{order['request_id']}: {order['internal_status']} | LPNs: {order['lpns']}")
```

### Listings with anti-oscillation
```python
from src.extractors import WallapopListingsExtractor, StoredListing

existing = {"LPNWE001": StoredListing(lpn="LPNWE001", product_id="old-id")}

extractor = WallapopListingsExtractor(
    bearer_token="token", user_agents=["UA/1"], account_id=1,
)
result = extractor.extract(existing_listings=existing)
for update in result["updates"]:
    print(f"{update.lpn}: {update.action}")
```

## Tests
```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

91 tests covering: LPN regex, price splitting, carrier detection, deadline parsing, label URLs, bilingual dates, status mapping, anti-oscillation, change detection, bundle hash extraction, base client.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Flexible LPN regex | Must capture Amazon FBA (`LPNWE...`), manual (`LPN1SIKA3`), and location suffixes |
| `previous_product_id` tracking | Wallapop forces resubmission every ~3 days; without oscillation detection, stats would double-count |
| Carrier detection from icon URL | More reliable than text matching; Wallapop A/B tests description text |
| Biased delay distribution | 70/30 split between short/long delays mimics real human browsing patterns |
| Callbacks for persistence | Keeps extractors database-agnostic; caller controls storage |
| Token refresh callback | Allows pluggable auth (cookies, OAuth, manual) without coupling extractors to auth logic |
