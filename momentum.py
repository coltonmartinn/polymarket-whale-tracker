"""
Persists each scan's whale snapshots and diffs consecutive scans to detect
whales adding to, reducing, entering, or exiting near-certain positions.
This is the piece that turns a single snapshot into a time series.
"""

from db import get_conn, now_iso

MIN_MOVE_USD = 250  # ignore noise-level position changes below this, for the Momentum-tab event feed

# Per-row trend labeling (every whale gets one of these, not just the ones
# that moved enough to make the event feed above).
STABLE_MIN_ABS = 250    # a move smaller than this is never "increasing"/"decreasing"
STABLE_MIN_PCT = 0.05   # ...nor is a move smaller than 5% of the prior position

BUCKET_ORDER = ["Low", "Medium", "High", "Very High"]
MOMENTUM_SHIFT_THRESHOLD = 0.25  # net momentum as a fraction of current whale $ needed to shift a bucket


def record_scan(signals, meta):
    """Persist a completed scan's signals as a scan row + per-whale snapshot rows."""
    scanned_at = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scans (scanned_at, price_threshold, min_whale_usd, markets_scanned, markets_flagged) "
            "VALUES (?, ?, ?, ?, ?)",
            (scanned_at, meta["price_threshold"], meta["min_whale_usd"], meta["markets_scanned"], meta["markets_flagged"]),
        )
        scan_id = cur.lastrowid

        snapshot_rows = []
        for s in signals:
            for w in s["whales"]:
                snapshot_rows.append((
                    scan_id, s["condition_id"], s["clob_token_id"],
                    s["market"], s["slug"], s["outcome"], s["outcome_type"], s["implied_probability"],
                    s["liquidity"], s["volume"], w["wallet"], w["name"], w["shares"], w["usd_value"], scanned_at,
                ))
        conn.executemany(
            """INSERT INTO snapshots
               (scan_id, condition_id, clob_token_id, market_question, slug, outcome_name, outcome_type,
                price, liquidity, volume, wallet, wallet_name, shares, usd_value, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            snapshot_rows,
        )

        for s in signals:
            existing = conn.execute(
                "SELECT clob_token_id FROM flagged_markets WHERE clob_token_id = ?", (s["clob_token_id"],)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO flagged_markets
                       (clob_token_id, condition_id, question, slug, outcome_name, first_flagged_at,
                        last_seen_at, first_flagged_price, had_whale_momentum, last_checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (s["clob_token_id"], s["condition_id"], s["market"], s["slug"], s["outcome"],
                     scanned_at, scanned_at, s["implied_probability"], scanned_at),
                )
            else:
                conn.execute(
                    "UPDATE flagged_markets SET last_seen_at = ? WHERE clob_token_id = ?",
                    (scanned_at, s["clob_token_id"]),
                )

        return scan_id


def _previous_scan_id(conn, current_scan_id):
    row = conn.execute(
        "SELECT id FROM scans WHERE id < ? ORDER BY id DESC LIMIT 1", (current_scan_id,)
    ).fetchone()
    return row["id"] if row else None


def compute_momentum(current_scan_id):
    """
    Diff the given scan against the one immediately before it. Returns a list
    of events: new whale entries, position increases/decreases, and exits,
    each carrying enough market context to render in the UI.
    """
    with get_conn() as conn:
        prev_id = _previous_scan_id(conn, current_scan_id)
        if prev_id is None:
            return {"has_previous": False, "events": []}

        curr_rows = {
            (r["clob_token_id"], r["wallet"]): dict(r)
            for r in conn.execute("SELECT * FROM snapshots WHERE scan_id = ?", (current_scan_id,))
        }
        prev_rows = {
            (r["clob_token_id"], r["wallet"]): dict(r)
            for r in conn.execute("SELECT * FROM snapshots WHERE scan_id = ?", (prev_id,))
        }

        events = []
        for key, curr in curr_rows.items():
            prev = prev_rows.get(key)
            if prev is None:
                events.append({
                    "type": "new_entry",
                    "market": curr["market_question"],
                    "slug": curr["slug"],
                    "clob_token_id": curr["clob_token_id"],
                    "outcome": curr["outcome_name"],
                    "wallet": curr["wallet"],
                    "wallet_name": curr["wallet_name"],
                    "usd_value": curr["usd_value"],
                    "delta_usd": curr["usd_value"],
                })
                continue
            delta = curr["usd_value"] - prev["usd_value"]
            if abs(delta) < MIN_MOVE_USD:
                continue
            events.append({
                "type": "increased" if delta > 0 else "decreased",
                "market": curr["market_question"],
                "slug": curr["slug"],
                "clob_token_id": curr["clob_token_id"],
                "outcome": curr["outcome_name"],
                "wallet": curr["wallet"],
                "wallet_name": curr["wallet_name"],
                "usd_value": curr["usd_value"],
                "delta_usd": round(delta, 2),
            })

        for key, prev in prev_rows.items():
            if key not in curr_rows:
                events.append({
                    "type": "exited",
                    "market": prev["market_question"],
                    "slug": prev["slug"],
                    "clob_token_id": prev["clob_token_id"],
                    "outcome": prev["outcome_name"],
                    "wallet": prev["wallet"],
                    "wallet_name": prev["wallet_name"],
                    "usd_value": 0,
                    "delta_usd": -prev["usd_value"],
                })

        events.sort(key=lambda e: abs(e["delta_usd"]), reverse=True)

        moved_keys = {
            (e["clob_token_id"], e["wallet"]) for e in events if e["type"] in ("increased", "decreased")
        }
        unchanged_count = sum(
            1 for key in curr_rows if key in prev_rows and key not in moved_keys
        )

        moved_token_ids = {
            e["clob_token_id"] for e in events if e["type"] in ("new_entry", "increased")
        }
        if moved_token_ids:
            conn.execute(
                f"UPDATE flagged_markets SET had_whale_momentum = 1 WHERE clob_token_id IN "
                f"({','.join('?' for _ in moved_token_ids)})",
                tuple(moved_token_ids),
            )

        return {
            "has_previous": True,
            "previous_scan_id": prev_id,
            "current_scan_id": current_scan_id,
            "events": events,
            "unchanged_count": unchanged_count,
        }


def latest_momentum():
    """Convenience for the dashboard: diff the two most recent scans without running a new one."""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return {"has_previous": False, "events": []}
    return compute_momentum(row["id"])


def classify_trend(delta_usd, prior_usd):
    """
    Label a single whale's position change. Unlike compute_momentum's event
    feed (which only lists moves past a flat $250 filter worth surfacing as
    an event), every whale gets exactly one of these labels, always.
    """
    if prior_usd is None:
        return "new"
    threshold = max(STABLE_MIN_ABS, STABLE_MIN_PCT * prior_usd)
    if delta_usd > threshold:
        return "increasing"
    if delta_usd < -threshold:
        return "decreasing"
    return "stable"


def apply_momentum(signals, scan_id):
    """
    Mutates `signals` in place (the shape build_signals()/analyzer.py
    produces: a list of per-outcome dicts each with a "whales" list):

      - attaches w["trend"] to every whale ("new"/"increasing"/"decreasing"/"stable")
      - lets net momentum since the previous scan shift (not replace) each
        outcome's signal_strength bucket by at most one level, recording the
        pre-shift value as base_signal_strength and the shift direction (if
        any) as momentum_shifted

    Degrades gracefully when there's no previous scan (first-ever run):
    every whale is labeled "new", momentum_pct is 0, and no bucket shifts.
    """
    with get_conn() as conn:
        prev_id = _previous_scan_id(conn, scan_id)
        prev_rows = {}
        if prev_id is not None:
            prev_rows = {
                (r["clob_token_id"], r["wallet"]): r["usd_value"]
                for r in conn.execute("SELECT * FROM snapshots WHERE scan_id = ?", (prev_id,))
            }

    for s in signals:
        token_id = s["clob_token_id"]
        net_momentum = 0.0

        for w in s["whales"]:
            prior_usd = prev_rows.get((token_id, w["wallet"]))
            delta = w["usd_value"] - (prior_usd or 0)
            w["trend"] = classify_trend(delta, prior_usd)
            if w["trend"] in ("new", "increasing", "decreasing"):
                net_momentum += delta

        if prev_id is not None:
            current_wallets = {w["wallet"] for w in s["whales"]}
            exited_usd = sum(
                usd for (tok, wallet), usd in prev_rows.items()
                if tok == token_id and wallet not in current_wallets
            )
            net_momentum -= exited_usd

        whale_usd_total = s.get("whale_usd_total") or 0
        momentum_pct = (net_momentum / whale_usd_total) if whale_usd_total else 0.0
        s["momentum_pct"] = round(momentum_pct, 4)
        s["momentum_shifted"] = None

        if "signal_strength" in s:
            s["base_signal_strength"] = s["signal_strength"]
            if prev_id is not None and s["signal_strength"] in BUCKET_ORDER:
                idx = BUCKET_ORDER.index(s["signal_strength"])
                if momentum_pct >= MOMENTUM_SHIFT_THRESHOLD and idx < len(BUCKET_ORDER) - 1:
                    s["signal_strength"] = BUCKET_ORDER[idx + 1]
                    s["momentum_shifted"] = "up"
                elif momentum_pct <= -MOMENTUM_SHIFT_THRESHOLD and idx > 0:
                    s["signal_strength"] = BUCKET_ORDER[idx - 1]
                    s["momentum_shifted"] = "down"

    return signals
