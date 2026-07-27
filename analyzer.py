"""
Turns raw market + holder data into ranked "signals": near-certain outcomes
where large wallets hold a meaningful position.

Important honesty check: the "implied probability" shown here IS the
market's own price -- we are not predicting anything beyond what the market
already prices in. The value this adds is surfacing *where big wallets are
concentrated* relative to that price, as a secondary signal, not a superior
probability estimate.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import polymarket_client as pm

MAX_HOLDER_WORKERS = 8

# Verified live (2026-07-27): /holders has no separate "total count" endpoint,
# but the returned count plateaus at the true total once the requested limit
# exceeds it (tested up to 5000 with no cap or slowdown observed, edge-cached
# by Cloudflare) -- so fetching a generous limit doubles as an exact holder
# count for all but the very largest markets.
HOLDER_FETCH_LIMIT = 500


def _signal_strength(whale_usd_total, pct_of_liquidity):
    """
    Bucket a raw conviction score into a human label. The score rewards both
    absolute whale dollars and how much of the market's own liquidity those
    wallets represent, so a big position in a thin market ranks higher than
    the same dollars in a deep one.
    """
    score = whale_usd_total * (1 + min(pct_of_liquidity / 100, 1))
    if score >= 50000:
        return "Very High", score
    if score >= 10000:
        return "High", score
    if score >= 2500:
        return "Medium", score
    return "Low", score


def _recommendation(outcome_type, outcome_name, price, whale_usd_total, whale_count, strength):
    payout_per_100 = round((1 / price - 1) * 100) if price > 0 else 0

    if outcome_type == "favorite":
        base = (
            f"Market is pricing \"{outcome_name}\" as a near-lock ({price:.1%}). "
            f"$100 on this returns ~${100 + payout_per_100} if it hits."
        )
        if strength in ("High", "Very High"):
            return base + f" {whale_count} large wallet(s) totaling ${whale_usd_total:,.0f} agree with the consensus."
        if strength == "Medium":
            return base + " Some whale backing, but not overwhelming."
        return base + " Limited whale conviction data on this side -- consensus is priced in, but not visibly whale-backed."

    base = (
        f"\"{outcome_name}\" is priced as a longshot ({price:.1%}). "
        f"$100 on this returns ~${100 + payout_per_100} if it hits."
    )
    if strength in ("High", "Very High"):
        return base + f" Contrarian signal: {whale_count} large wallet(s) totaling ${whale_usd_total:,.0f} are betting against the crowd."
    if strength == "Medium":
        return base + " Some contrarian whale interest, but modest."
    return base + " No significant whale conviction found on this side yet."


def _fetch_holders_for_market(condition_id):
    return condition_id, pm.get_top_holders(condition_id, limit=HOLDER_FETCH_LIMIT)


def build_signals(
    price_threshold=0.95,
    min_whale_usd=1000,
    holders_per_market=10,
    max_markets=200,
    progress_cb=None,
):
    """
    Scan active markets and return a list of ranked signal dicts, plus a
    small meta dict describing the scan. progress_cb(stage, done, total) is
    called periodically if provided, for UI progress reporting.
    """
    if progress_cb:
        progress_cb("fetching_markets", 0, 1)
    markets = pm.get_active_markets(max_markets=max_markets)

    flagged_by_market = {}
    market_meta = {}
    for m in markets:
        for outcome in pm.extract_high_probability_outcomes(m, price_threshold):
            flagged_by_market.setdefault(outcome["condition_id"], []).append(outcome)
            market_meta[outcome["condition_id"]] = {
                "question": outcome["question"],
                "slug": outcome["slug"],
                "liquidity": outcome["liquidity"],
                "volume": outcome["volume"],
                "end_date": outcome["end_date"],
            }

    total_markets = len(flagged_by_market)
    if progress_cb:
        progress_cb("fetching_holders", 0, max(total_markets, 1))

    holders_by_market = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_HOLDER_WORKERS) as pool:
        futures = [
            pool.submit(_fetch_holders_for_market, cid)
            for cid in flagged_by_market
        ]
        for fut in as_completed(futures):
            cid, holders_by_token = fut.result()
            holders_by_market[cid] = holders_by_token
            done += 1
            if progress_cb:
                progress_cb("fetching_holders", done, total_markets)

    signals = []
    for condition_id, outcomes in flagged_by_market.items():
        holders_by_token = holders_by_market.get(condition_id, {})
        meta = market_meta[condition_id]
        for outcome in outcomes:
            holders = holders_by_token.get(outcome["clob_token_id"], [])
            # every wallet holding ANY position, not just whales -- "how many
            # total people have this bet placed"
            total_holders = len(holders)
            total_holders_capped = total_holders >= HOLDER_FETCH_LIMIT

            whales = []
            for h in holders:
                shares = float(h.get("amount", 0) or 0)
                usd_value = shares * outcome["price"]
                if usd_value < min_whale_usd:
                    continue
                whales.append({
                    "wallet": h.get("proxyWallet", "unknown"),
                    "name": h.get("name") or h.get("pseudonym") or "",
                    "shares": round(shares, 2),
                    "usd_value": round(usd_value, 2),
                })
            if not whales:
                continue

            whales.sort(key=lambda w: w["usd_value"], reverse=True)
            # whale_count/whale_usd_total reflect every qualifying whale found
            # in the full holder list, even though the displayed `whales`
            # list below is truncated to holders_per_market for readability.
            whale_count = len(whales)
            whale_usd_total = round(sum(w["usd_value"] for w in whales), 2)
            whales = whales[:holders_per_market]
            liquidity = meta["liquidity"]
            pct_of_liquidity = (whale_usd_total / liquidity * 100) if liquidity > 0 else 0
            strength, score = _signal_strength(whale_usd_total, pct_of_liquidity)
            outcome_type = "favorite" if outcome["price"] >= 0.5 else "longshot"

            signals.append({
                "condition_id": condition_id,
                "market": meta["question"],
                "slug": meta["slug"],
                "end_date": meta["end_date"],
                # Verified live (2026-07-27): /event/{slug} 404s when slug is the
                # market's own slug rather than its parent event's -- /market/{slug}
                # resolves correctly in both the grouped-event and standalone cases.
                "polymarket_url": f"https://polymarket.com/market/{meta['slug']}" if meta["slug"] else None,
                "clob_token_id": outcome["clob_token_id"],
                "outcome": outcome["outcome_name"],
                "outcome_type": outcome_type,
                "implied_probability": outcome["price"],
                "payout_per_100": round((1 / outcome["price"] - 1) * 100) if outcome["price"] > 0 else None,
                "liquidity": round(liquidity, 2),
                "volume": round(meta["volume"], 2),
                "whale_count": whale_count,
                "whale_usd_total": whale_usd_total,
                "total_holders": total_holders,
                "total_holders_capped": total_holders_capped,
                "pct_of_liquidity": round(pct_of_liquidity, 1),
                "top_whale": whales[0],
                "whales": whales,
                "signal_strength": strength,
                "score": round(score, 2),
                "recommendation": _recommendation(
                    outcome_type, outcome["outcome_name"], outcome["price"],
                    whale_usd_total, whale_count, strength,
                ),
            })

    signals.sort(key=lambda s: s["score"], reverse=True)

    meta_out = {
        "markets_scanned": len(markets),
        "markets_flagged": total_markets,
        "signals_found": len(signals),
        "price_threshold": price_threshold,
        "min_whale_usd": min_whale_usd,
    }
    return signals, meta_out
