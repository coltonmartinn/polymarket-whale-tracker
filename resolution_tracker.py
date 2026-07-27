"""
Checks markets we've flagged as near-certain-with-whales against the Gamma
API to see if they've since resolved. When one resolves, records whether the
whale-backed outcome actually won -- this is the live, forward-looking twin
of backtest.py's historical calibration check, built from our own scans
instead of history, and specifically taggable by whether whale momentum was
observed (source='live_tracking' in the same calibration_observations table).
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

import polymarket_client as pm
from db import get_conn, now_iso

RESOLUTION_TOLERANCE = 0.02
MAX_CHECK_WORKERS = 10


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
        newly_resolved += 1

    return {"checked": checked, "newly_resolved": newly_resolved, "still_pending": checked - newly_resolved}


def get_tracking_status():
    with get_conn() as conn:
        pending = conn.execute("SELECT COUNT(*) c FROM flagged_markets WHERE resolved = 0").fetchone()["c"]
        resolved = conn.execute("SELECT COUNT(*) c FROM flagged_markets WHERE resolved = 1").fetchone()["c"]
        earliest = conn.execute("SELECT MIN(first_flagged_at) t FROM flagged_markets").fetchone()["t"]
    return {"pending": pending, "resolved": resolved, "tracking_since": earliest}


if __name__ == "__main__":
    print(check_resolutions())
