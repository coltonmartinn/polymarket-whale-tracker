"""
Thin client for Polymarket's public, read-only Gamma and Data APIs.

Field shapes here were verified against live responses on 2026-07-24 -- see
collector.py's commit history / conversation notes for what was wrong in the
first draft (holders are grouped by outcome token, not a flat list; 'amount'
is a share count, not a USD value).
"""

import json
import time

import requests

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

REQUEST_TIMEOUT = 15

# Verified live (2026-07-27): /prices-history rejects any startTs/endTs window
# longer than 15 days, regardless of fidelity -- returns 400 with an empty
# body. Callers must chunk longer ranges themselves.
MAX_HISTORY_WINDOW_DAYS = 15


def get_active_markets(max_markets=200, page_size=100, request_pause=0.15):
    """Page through active, unclosed markets from the Gamma API."""
    markets = []
    offset = 0
    while len(markets) < max_markets:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": page_size,
                "offset": offset,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        markets.extend(batch)
        offset += page_size
        if len(batch) < page_size:
            break
        time.sleep(request_pause)
    return markets[:max_markets]


def extract_high_probability_outcomes(market, price_threshold):
    """
    Return a list of dicts -- one per outcome priced at/above price_threshold
    or at/below (1 - price_threshold) -- each with condition_id, question,
    slug, liquidity, volume, outcome_index, outcome_name, price, and
    clob_token_id.
    """
    condition_id = market.get("conditionId")
    question = market.get("question", "")
    if not condition_id:
        return []

    raw_outcomes = market.get("outcomes")
    raw_prices = market.get("outcomePrices")
    raw_token_ids = market.get("clobTokenIds")
    if not raw_outcomes or not raw_prices or not raw_token_ids:
        return []

    try:
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        token_ids = json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else raw_token_ids
        prices = [float(p) for p in prices]
    except (ValueError, TypeError):
        return []

    if not (len(outcomes) == len(prices) == len(token_ids)):
        return []

    events = market.get("events") or []
    slug = market.get("slug") or (events[0].get("slug") if events else "")

    hits = []
    for i, p in enumerate(prices):
        if p >= price_threshold or p <= (1 - price_threshold):
            hits.append({
                "condition_id": condition_id,
                "question": question,
                "slug": slug,
                "liquidity": float(market.get("liquidityNum") or 0),
                "volume": float(market.get("volumeNum") or 0),
                "outcome_index": i,
                "outcome_name": outcomes[i],
                "price": p,
                "clob_token_id": token_ids[i],
            })
    return hits


def get_closed_markets(max_markets=300, page_size=100, request_pause=0.15):
    """Page through closed (resolved) markets from the Gamma API, newest first."""
    markets = []
    offset = 0
    while len(markets) < max_markets:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={
                "closed": "true",
                "limit": page_size,
                "offset": offset,
                "order": "volumeNum",
                "ascending": "false",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        markets.extend(batch)
        offset += page_size
        if len(batch) < page_size:
            break
        time.sleep(request_pause)
    return markets[:max_markets]


def get_price_history(token_id, start_ts, end_ts, fidelity=180):
    """
    Price history for one outcome token between start_ts and end_ts (unix
    seconds). Returns a list of {"t": unix_seconds, "p": price} points.
    Caller must keep (end_ts - start_ts) <= MAX_HISTORY_WINDOW_DAYS.
    """
    resp = requests.get(
        f"{CLOB_URL}/prices-history",
        params={
            "market": token_id,
            "startTs": int(start_ts),
            "endTs": int(end_ts),
            "fidelity": fidelity,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("history", [])


def get_top_holders(condition_id, limit=10):
    """
    Returns {clobTokenId: [holder_dict, ...]} for every outcome token in the
    market. 'amount' on a holder is a share count -- multiply by that
    outcome's price to get USD exposure.
    """
    resp = requests.get(
        f"{DATA_URL}/holders",
        params={"market": condition_id, "limit": limit},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return {}
    groups = resp.json()
    return {g.get("token"): g.get("holders", []) for g in groups}
