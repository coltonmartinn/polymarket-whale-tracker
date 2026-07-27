"""
Polymarket whale-position collector.

Finds markets trading at a high implied probability (near-certain YES or NO),
then pulls the largest holders in each of those markets.

No API key needed -- Gamma and Data API are both public read-only endpoints.

Docs:
  Gamma API : https://gamma-api.polymarket.com
  Data API  : https://data-api.polymarket.com
"""

import time
import csv
import requests

from momentum import record_scan, apply_momentum

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"

# --- tunables ---
PRICE_THRESHOLD = 0.95      # flag markets priced >= this or <= (1 - this)
MIN_HOLDER_BALANCE = 1000   # USDC-equivalent value floor to count as a "whale"
HOLDERS_PER_MARKET = 10     # top N holders to pull per market
MAX_MARKETS_TO_SCAN = 200   # how many active markets to page through per run
REQUEST_PAUSE = 0.25        # seconds between requests, be polite to the API


def get_active_markets(limit=100, max_pages=5):
    """Page through active, unclosed markets from the Gamma API."""
    markets = []
    offset = 0
    for _ in range(max_pages):
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
            },
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        markets.extend(batch)
        offset += limit
        if len(markets) >= MAX_MARKETS_TO_SCAN:
            break
        time.sleep(REQUEST_PAUSE)
    return markets[:MAX_MARKETS_TO_SCAN]


def extract_high_probability_outcomes(market):
    """
    Given a Gamma market record, return a list of dicts -- one per outcome
    priced at or beyond PRICE_THRESHOLD (or at/below 1 - PRICE_THRESHOLD) --
    each with condition_id, question, outcome_index, outcome_name, price,
    and clob_token_id.

    Verified against a live Gamma response (2026-07-24): 'outcomes',
    'outcomePrices', and 'clobTokenIds' are all JSON-encoded string arrays,
    positionally aligned with each other (index 0 = "Yes", index 1 = "No",
    etc. for binary markets; more entries for multi-outcome markets).
    """
    import json

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
        if p >= PRICE_THRESHOLD or p <= (1 - PRICE_THRESHOLD):
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


def get_top_holders(condition_id, limit=HOLDERS_PER_MARKET):
    """
    Pull the largest position holders for a given market (by conditionId).

    Verified against a live Data API response (2026-07-24): the endpoint
    returns a list grouped by outcome token, not a flat holder list:

        [{"token": "<clobTokenId>", "holders": [{"proxyWallet": ...,
          "amount": <float, share count>, "outcomeIndex": <int>, ...}, ...]},
         ...]

    One call returns holders for every outcome token in the market, so it
    only needs to be called once per flagged market (not once per flagged
    outcome). 'amount' is the number of outcome shares held, NOT a USD
    value -- multiply by the outcome's price to get USD exposure.
    """
    resp = requests.get(
        f"{DATA_URL}/holders",
        params={
            "market": condition_id,
            "limit": limit,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return {}
    groups = resp.json()
    # map clobTokenId -> list of holder dicts
    return {g.get("token"): g.get("holders", []) for g in groups}


def main():
    print("Fetching active markets...")
    markets = get_active_markets()
    print(f"  {len(markets)} active markets pulled")

    # group flagged outcomes by market so we call /holders once per market
    flagged_by_market = {}
    for m in markets:
        for outcome in extract_high_probability_outcomes(m):
            flagged_by_market.setdefault(outcome["condition_id"], []).append(outcome)

    flagged_outcome_count = sum(len(v) for v in flagged_by_market.values())
    print(
        f"  {flagged_outcome_count} outcomes across {len(flagged_by_market)} markets "
        f">= {PRICE_THRESHOLD:.0%} (or <= {1 - PRICE_THRESHOLD:.0%}) implied probability"
    )

    rows = []
    # keyed by clob_token_id (not condition_id) -- a market can have two
    # flagged outcomes (e.g. favorite AND longshot side both near-certain),
    # and momentum needs to track each side's whales separately.
    signals_by_token = {}
    for condition_id, outcomes in flagged_by_market.items():
        holders_by_token = get_top_holders(condition_id)
        time.sleep(REQUEST_PAUSE)
        for outcome in outcomes:
            holders = holders_by_token.get(outcome["clob_token_id"], [])
            for h in holders:
                shares = float(h.get("amount", 0) or 0)
                usd_value = shares * outcome["price"]
                if usd_value < MIN_HOLDER_BALANCE:
                    continue
                rows.append({
                    "market": outcome["question"],
                    "condition_id": condition_id,
                    "outcome": outcome["outcome_name"],
                    "implied_probability": outcome["price"],
                    "wallet": h.get("proxyWallet", "unknown"),
                    "shares": shares,
                    "usd_value": round(usd_value, 2),
                    "clob_token_id": outcome["clob_token_id"],
                })

                sig = signals_by_token.setdefault(outcome["clob_token_id"], {
                    "condition_id": condition_id,
                    "clob_token_id": outcome["clob_token_id"],
                    "market": outcome["question"],
                    "slug": outcome["slug"],
                    "outcome": outcome["outcome_name"],
                    "outcome_type": "favorite" if outcome["price"] >= 0.5 else "longshot",
                    "implied_probability": outcome["price"],
                    "liquidity": outcome["liquidity"],
                    "volume": outcome["volume"],
                    "whales": [],
                    "signal_strength": None,  # collector.py doesn't score buckets; momentum-shift is a no-op here
                })
                sig["whales"].append({
                    "wallet": h.get("proxyWallet", "unknown"),
                    "name": h.get("name") or h.get("pseudonym") or "",
                    "shares": shares,
                    "usd_value": round(usd_value, 2),
                })

    if not rows:
        print("No whale positions found above threshold this run.")
        return

    for sig in signals_by_token.values():
        sig["whale_usd_total"] = round(sum(w["usd_value"] for w in sig["whales"]), 2)
    signals = list(signals_by_token.values())

    scan_id = record_scan(signals, {
        "price_threshold": PRICE_THRESHOLD,
        "min_whale_usd": MIN_HOLDER_BALANCE,
        "markets_scanned": len(markets),
        "markets_flagged": len(flagged_by_market),
    })
    apply_momentum(signals, scan_id)  # attaches .trend to each whale dict in place; degrades to all-"new" on first run

    trend_by_key = {
        (sig["clob_token_id"], w["wallet"]): w["trend"]
        for sig in signals for w in sig["whales"]
    }
    for row in rows:
        row["trend"] = trend_by_key.get((row["clob_token_id"], row["wallet"]), "new")

    out_path = "whale_positions.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path} (scan_id={scan_id})")


if __name__ == "__main__":
    main()
