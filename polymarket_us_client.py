"""
Thin client for Polymarket US's public, no-auth Gateway API
(gateway.polymarket.us). Verified live and genuinely public on 2026-07-27 --
same no-auth model as Global's Gamma API, and a similar market shape
(question/slug/endDate/active/closed/category), but a completely separate
catalog with no shared IDs. This is the CFTC-regulated venue (QCX LLC)
actually tradeable by US residents, distinct from Polymarket Global
(polymarket_client.py) where our whale data lives -- Global is geo-blocked
to US persons for trading. See market_mapper.py for how the two get linked.

Verified live (2026-07-27): the `category` query param does NOT filter
server-side (a request with category=politics still returned sports
markets), so category filtering here is done client-side. Also verified:
of the first 500 active markets, 434 were sports, 53 politics, 7 culture,
6 macro -- the catalog is heavily sports-skewed, which is why callers
generally want NON_SPORTS_CATEGORIES rather than everything.
"""

import time

import requests

GATEWAY_URL = "https://gateway.polymarket.us"
REQUEST_TIMEOUT = 15

NON_SPORTS_CATEGORIES = {"politics", "macro", "culture"}


def get_active_markets(max_markets=3000, page_size=100, request_pause=0.15,
                        categories=NON_SPORTS_CATEGORIES, max_pages=100):
    """
    Page through active, unclosed US markets, filtered client-side to
    `categories` (pass None for no filtering). max_pages is a sanity cap on
    total requests since, with client-side filtering and a sports-heavy
    catalog, there's no way to know from the API alone how many pages it
    takes to gather enough non-sports markets.
    """
    markets = []
    offset = 0
    pages = 0
    while len(markets) < max_markets and pages < max_pages:
        resp = requests.get(
            f"{GATEWAY_URL}/v1/markets",
            params={"active": "true", "closed": "false", "limit": page_size, "offset": offset},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw_batch = resp.json().get("markets", [])
        pages += 1
        if not raw_batch:
            break
        if categories is not None:
            markets.extend(m for m in raw_batch if m.get("category") in categories)
        else:
            markets.extend(raw_batch)
        offset += page_size
        if len(raw_batch) < page_size:
            break
        time.sleep(request_pause)
    return markets[:max_markets]
