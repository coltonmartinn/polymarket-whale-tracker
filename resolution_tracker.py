"""
Checks markets we've flagged as near-certain-with-whales against the Gamma
API to see if they've since resolved. When one resolves, records whether the
whale-backed outcome actually won -- this is the live, forward-looking twin
of backtest.py's historical calibration check, built from our own scans
instead of history, and specifically taggable by whether whale momentum was
observed (source='live_tracking' in the same calibration_observations table).

It also tags the individual wallets that were holding the resolved position
(wallet_calls table), which is what powers the whale leaderboard: not just
"was the whale-backed side right," but "which specific whales have a track
record of being right."
"""

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

import polymarket_client as pm
from db import get_conn, now_iso

RESOLUTION_TOLERANCE = 0.02
MAX_CHECK_WORKERS = 10
DEFAULT_MIN_CALLS = 3  # wallets need at least this many resolved calls to appear on the leaderboard


def _check_one(condition_id):
    resp = requests.get(
        f"{pm.GAMMA_URL}/markets",
        params={"condition_ids": condition_id},
        timeout=pm.REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    batch = resp.json()
    if not batch:
        return None
    return batch[0]


def check_resolutions():
    with get_conn() as conn:
        pending = [
            dict(r) for r in conn.execute(
                "SELECT * FROM flagged_markets WHERE resolved = 0"
            ).fetchall()
        ]

    # dedupe network calls: multiple flagged outcomes can share a condition_id
    unique_condition_ids = {row["condition_id"] for row in pending}
    markets_by_condition_id = {}
    with ThreadPoolExecutor(max_workers=MAX_CHECK_WORKERS) as pool:
        futures = {pool.submit(_check_one, cid): cid for cid in unique_condition_ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            markets_by_condition_id[cid] = fut.result()

    checked = 0
    newly_resolved = 0
    for row in pending:
        market = markets_by_condition_id.get(row["condition_id"])
        checked += 1
        if market is None or not market.get("closed"):
            with get_conn() as conn:
                conn.execute(
                    "UPDATE flagged_markets SET last_checked_at = ? WHERE clob_token_id = ?",
                    (now_iso(), row["clob_token_id"]),
                )
            continue

        try:
            outcomes = json.loads(market.get("outcomes") or "[]")
            prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
            token_ids = json.loads(market.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue

        try:
            our_idx = token_ids.index(row["clob_token_id"])
        except ValueError:
            continue

        our_price = prices[our_idx]
        won = None
        if our_price >= 1 - RESOLUTION_TOLERANCE:
            won = 1
        elif our_price <= RESOLUTION_TOLERANCE:
            won = 0
        if won is None:
            continue  # voided / ambiguous resolution -- leave pending

        resolved_at = now_iso()
        first_flagged_dt = datetime.fromisoformat(row["first_flagged_at"])
        days_before = (datetime.now(timezone.utc) - first_flagged_dt).total_seconds() / 86400

        with get_conn() as conn:
            conn.execute(
                """UPDATE flagged_markets
                   SET resolved = 1, resolved_won = ?, resolved_at = ?, last_checked_at = ?
                   WHERE clob_token_id = ?""",
                (won, resolved_at, resolved_at, row["clob_token_id"]),
            )
            conn.execute(
                """INSERT INTO calibration_observations
                   (condition_id, question, outcome_name, price, days_before_resolution, won, observed_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'live_tracking')""",
                (row["condition_id"], row["question"], row["outcome_name"], row["first_flagged_price"],
                 round(days_before, 3), won, row["first_flagged_at"]),
            )

            # One row per wallet that was ever seen holding this resolved
            # position, keyed on their most recent snapshot (final conviction
            # level before resolution). This is what the leaderboard rolls up.
            wallet_positions = {}
            for snap in conn.execute(
                "SELECT wallet, wallet_name, usd_value FROM snapshots WHERE clob_token_id = ? ORDER BY scan_id ASC",
                (row["clob_token_id"],),
            ):
                wallet_positions[snap["wallet"]] = snap  # later rows overwrite -> latest per wallet
            conn.executemany(
                """INSERT OR IGNORE INTO wallet_calls
                   (wallet, wallet_name, clob_token_id, condition_id, question, outcome_name, usd_value, won, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (wp["wallet"], wp["wallet_name"], row["clob_token_id"], row["condition_id"],
                     row["question"], row["outcome_name"], wp["usd_value"], won, resolved_at)
                    for wp in wallet_positions.values()
                ],
            )
        newly_resolved += 1

    return {"checked": checked, "newly_resolved": newly_resolved, "still_pending": checked - newly_resolved}


def _wilson_lower_bound(wins, n, z=1.96):
    """
    95%-confidence lower bound on win rate. Ranking by this instead of raw
    win_rate is what keeps a 1-for-1 wallet from outranking a 20-for-25 one --
    a small sample's raw rate is unreliable, and the lower bound discounts it
    accordingly without throwing the wallet out entirely.
    """
    if n == 0:
        return 0.0
    phat = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    return (center - margin) / denom


def get_wallet_leaderboard(min_calls=DEFAULT_MIN_CALLS, limit=25):
    """
    Roll up wallet_calls (one row per wallet per resolved market they held a
    near-certain position in) into a per-wallet win rate, ranked by Wilson
    lower bound so small samples don't dominate. Wallets below min_calls are
    tracked but excluded from the ranked list -- not enough signal yet.
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT wallet, wallet_name, usd_value, won FROM wallet_calls"
        ).fetchall()]

    by_wallet = {}
    for r in rows:
        w = by_wallet.setdefault(r["wallet"], {
            "wallet": r["wallet"], "wallet_name": "", "n": 0, "wins": 0, "total_usd": 0.0,
        })
        w["n"] += 1
        w["wins"] += r["won"]
        w["total_usd"] += r["usd_value"] or 0
        if r["wallet_name"] and not w["wallet_name"]:
            w["wallet_name"] = r["wallet_name"]

    leaderboard = []
    for w in by_wallet.values():
        if w["n"] < min_calls:
            continue
        w["win_rate"] = round(w["wins"] / w["n"], 4)
        w["avg_usd"] = round(w["total_usd"] / w["n"], 2)
        w["confidence"] = round(_wilson_lower_bound(w["wins"], w["n"]), 4)
        leaderboard.append(w)

    leaderboard.sort(key=lambda w: w["confidence"], reverse=True)

    return {
        "leaderboard": leaderboard[:limit],
        "min_calls": min_calls,
        "wallets_tracked": len(by_wallet),
        "wallets_qualifying": len(leaderboard),
    }


def get_tracking_status():
    with get_conn() as conn:
        pending = conn.execute("SELECT COUNT(*) c FROM flagged_markets WHERE resolved = 0").fetchone()["c"]
        resolved = conn.execute("SELECT COUNT(*) c FROM flagged_markets WHERE resolved = 1").fetchone()["c"]
        earliest = conn.execute("SELECT MIN(first_flagged_at) t FROM flagged_markets").fetchone()["t"]
    return {"pending": pending, "resolved": resolved, "tracking_since": earliest}


if __name__ == "__main__":
    print(check_resolutions())
