"""
Calibration backtest: for already-resolved binary markets, look at the final
15 days of price history before resolution, find every point where price sat
at/beyond the near-certain threshold (>=95% or <=5%), and check whether the
market actually resolved that way.

This answers a different question than the live scanner does. The scanner
finds *current* near-certain markets with whale positions. This asks: across
history, when Polymarket prices something near-certain, is it actually right
that often? That's the calibration check that turns "the market says 97%"
into "and historically, 97% calls have hit about 97% of the time" (or
haven't -- either result is informative).

Known scope limits, stated plainly rather than hidden:
  - Only binary (Yes/No) markets are included, to keep "the other outcome"
    unambiguous. Multi-outcome brackets are skipped.
  - Only the final 15 days before resolution are examined (a CLOB API
    constraint -- see polymarket_client.MAX_HISTORY_WINDOW_DAYS), not full
    market lifetime. A market that spent months at 97% but only entered our
    window at day 10 still gets fairly represented; one that spent a year in
    the 60s and only crossed 95% in its last hours will look like a "late"
    near-certain call because that's what it was.
  - Markets are sampled by volume (highest first) to avoid microcap/illiquid
    noise, which also means this skews toward high-profile markets.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import polymarket_client as pm
from db import get_conn, now_iso

PRICE_THRESHOLD = 0.95
FIDELITY_MINUTES = 180  # 3-hour points; 15 days / 3h = 120 points, well under the API's per-request cap
MAX_HISTORY_WORKERS = 8
RESOLUTION_TOLERANCE = 0.02  # outcomePrice must be within this of 0 or 1 to count as a clean resolution


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_resolved_binary_markets(max_markets, min_volume):
    import json

    raw_markets = pm.get_closed_markets(max_markets=max_markets * 3)  # over-fetch; many will be filtered out
    out = []
    for m in raw_markets:
        if len(out) >= max_markets:
            break
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            token_ids = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue

        if len(outcomes) != 2 or len(prices) != 2 or len(token_ids) != 2:
            continue
        if float(m.get("volumeNum") or 0) < min_volume:
            continue

        winner_idx = None
        if prices[0] >= 1 - RESOLUTION_TOLERANCE and prices[1] <= RESOLUTION_TOLERANCE:
            winner_idx = 0
        elif prices[1] >= 1 - RESOLUTION_TOLERANCE and prices[0] <= RESOLUTION_TOLERANCE:
            winner_idx = 1
        if winner_idx is None:
            continue  # ambiguous / voided / 50-50 -- skip

        end_dt = _parse_iso(m.get("endDate"))
        if end_dt is None:
            continue

        out.append({
            "condition_id": m.get("conditionId"),
            "question": m.get("question", ""),
            "outcomes": outcomes,
            "token_ids": token_ids,
            "winner_idx": winner_idx,
            "end_ts": int(end_dt.timestamp()),
        })
    return out


def _fetch_observations_for_market(market):
    end_ts = market["end_ts"]
    start_ts = end_ts - pm.MAX_HISTORY_WINDOW_DAYS * 86400

    observations = []
    for idx, token_id in enumerate(market["token_ids"]):
        history = pm.get_price_history(token_id, start_ts, end_ts, fidelity=FIDELITY_MINUTES)
        won = 1 if idx == market["winner_idx"] else 0
        for point in history:
            p = point.get("p")
            t = point.get("t")
            if p is None or t is None:
                continue
            p = float(p)
            if p >= PRICE_THRESHOLD or p <= (1 - PRICE_THRESHOLD):
                observations.append({
                    "condition_id": market["condition_id"],
                    "question": market["question"],
                    "outcome_name": market["outcomes"][idx],
                    "price": p,
                    "days_before_resolution": round((end_ts - t) / 86400, 3),
                    "won": won,
                    "observed_at": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
                })
    return observations


def run_backtest(max_markets=250, min_volume=5000, progress_cb=None):
    if progress_cb:
        progress_cb("fetching_resolved_markets", 0, 1)
    markets = _get_resolved_binary_markets(max_markets, min_volume)

    all_observations = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_HISTORY_WORKERS) as pool:
        futures = [pool.submit(_fetch_observations_for_market, m) for m in markets]
        for fut in as_completed(futures):
            all_observations.extend(fut.result())
            done += 1
            if progress_cb:
                progress_cb("fetching_price_history", done, len(markets))

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calibration_runs (run_at, markets_sampled, markets_used, observations) VALUES (?, ?, ?, ?)",
            (now_iso(), len(markets), len(markets), len(all_observations)),
        )
        conn.executemany(
            """INSERT INTO calibration_observations
               (condition_id, question, outcome_name, price, days_before_resolution, won, observed_at, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'historical_backtest')""",
            [
                (o["condition_id"], o["question"], o["outcome_name"], o["price"],
                 o["days_before_resolution"], o["won"], o["observed_at"])
                for o in all_observations
            ],
        )

    return {
        "markets_sampled": len(markets),
        "observations": len(all_observations),
    }


def _brier(observations):
    if not observations:
        return None
    return round(sum((o["price"] - o["won"]) ** 2 for o in observations) / len(observations), 4)


def get_calibration_summary(source=None):
    """Read back accumulated observations and bucket them into a reliability table."""
    with get_conn() as conn:
        query = "SELECT * FROM calibration_observations"
        params = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    if not rows:
        return {"observations": 0, "buckets": [], "brier_score": None, "by_lead_time": []}

    def bucket_for(price):
        if price >= PRICE_THRESHOLD:
            lo = int(price * 100)
            return f"{lo}-{lo + 1}%", lo
        else:
            hi = int(price * 100) + 1
            return f"{hi - 1}-{hi}%", hi

    buckets = {}
    for o in rows:
        label, sort_key = bucket_for(o["price"])
        b = buckets.setdefault(label, {"label": label, "sort_key": sort_key, "n": 0, "wins": 0})
        b["n"] += 1
        b["wins"] += o["won"]

    bucket_list = sorted(buckets.values(), key=lambda b: b["sort_key"])
    for b in bucket_list:
        b["observed_win_rate"] = round(b["wins"] / b["n"], 4)
        del b["sort_key"]

    lead_bins = [(0, 1), (1, 3), (3, 7), (7, 15)]
    by_lead_time = []
    for lo, hi in lead_bins:
        subset = [o for o in rows if lo <= o["days_before_resolution"] < hi]
        if not subset:
            continue
        wins = sum(o["won"] if o["price"] >= PRICE_THRESHOLD else (1 - o["won"]) for o in subset)
        by_lead_time.append({
            "label": f"{lo}-{hi} days before resolution",
            "n": len(subset),
            "accuracy": round(wins / len(subset), 4),
        })

    favorite_obs = [o for o in rows if o["price"] >= PRICE_THRESHOLD]
    longshot_obs = [o for o in rows if o["price"] < PRICE_THRESHOLD]

    # Raw point counts overweight markets that just sat at 99% for weeks --
    # every 3-hour sample during that streak isn't an independent "call".
    # Collapse to one row per (market, outcome side) using its most extreme
    # price in-window, so the headline accuracy reflects distinct near-certain
    # calls, not autocorrelated snapshots of the same call.
    calls = {}
    for o in rows:
        side = "favorite" if o["price"] >= PRICE_THRESHOLD else "longshot"
        key = (o["condition_id"], o["outcome_name"], side)
        existing = calls.get(key)
        more_extreme = (
            existing is None
            or (side == "favorite" and o["price"] > existing["price"])
            or (side == "longshot" and o["price"] < existing["price"])
        )
        if more_extreme:
            calls[key] = o

    call_rows = list(calls.values())
    fav_calls = [c for c in call_rows if c["price"] >= PRICE_THRESHOLD]
    long_calls = [c for c in call_rows if c["price"] < PRICE_THRESHOLD]

    return {
        "observations": len(rows),
        "markets": len({o["condition_id"] for o in rows}),
        "buckets": bucket_list,
        "brier_score": _brier(rows),
        "favorite_accuracy": round(sum(o["won"] for o in favorite_obs) / len(favorite_obs), 4) if favorite_obs else None,
        "favorite_n": len(favorite_obs),
        "longshot_hit_rate": round(sum(o["won"] for o in longshot_obs) / len(longshot_obs), 4) if longshot_obs else None,
        "longshot_n": len(longshot_obs),
        "by_lead_time": by_lead_time,
        "call_level": {
            "note": "One row per (market, outcome side), deduplicated to its most extreme in-window price -- avoids overcounting a market that sat near-certain for weeks as dozens of independent calls.",
            "distinct_calls": len(call_rows),
            "favorite_accuracy": round(sum(c["won"] for c in fav_calls) / len(fav_calls), 4) if fav_calls else None,
            "favorite_n": len(fav_calls),
            "longshot_hit_rate": round(sum(c["won"] for c in long_calls) / len(long_calls), 4) if long_calls else None,
            "longshot_n": len(long_calls),
            "brier_score": _brier(call_rows),
        },
    }


if __name__ == "__main__":
    started = time.time()

    def _progress(stage, done, total):
        print(f"  [{stage}] {done}/{total}")

    result = run_backtest(max_markets=250, min_volume=5000, progress_cb=_progress)
    print(f"Backtest done in {time.time() - started:.1f}s: {result}")
    print(get_calibration_summary(source="historical_backtest"))
