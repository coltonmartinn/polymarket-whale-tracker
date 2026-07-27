"""
Fake-money paper trading: two independent $-tracked portfolios so betting
behavior can be sanity-checked against live outcomes without risking real
money. "Manual" bets are placed by hand from the dashboard, on any
currently flagged signal. "Auto-follow" places a fixed-size bet
automatically, the first time a signal reaches AUTO_STRENGTH_QUALIFYING --
a mechanical test of "would blindly following strong whale signals have
made money," separate from anything a person chose to back.

Positions are valued at cost, not marked-to-market: opening a position is
value-neutral in this model, and money only moves on resolution (shares pay
$1 each on a win, $0 on a loss). That keeps the numbers honest -- nothing
here claims an unrealized gain from a price that could still reverse before
the market settles.
"""

from db import get_conn, now_iso

STARTING_BALANCE = 10000.0
AUTO_STAKE_USD = 250.0
AUTO_STRENGTH_QUALIFYING = ("High", "Very High")
# Verified live (2026-07-27): a single 200-market scan can flag 200+ signals
# at High/Very High strength at once -- without this cap, the very first
# scan would spend the entire starting balance in one shot, leaving nothing
# to observe "over time." Capping new bets per run makes the portfolio fill
# in gradually across scans instead; anything not picked this run just
# rolls over to the next one (it's never marked "already seen" until bet on).
MAX_NEW_AUTO_BETS_PER_RUN = 5

VALID_MODES = ("manual", "auto")


def _cash_available(conn, mode):
    open_stake = conn.execute(
        "SELECT COALESCE(SUM(stake_usd), 0) s FROM demo_bets WHERE mode = ? AND resolved = 0", (mode,)
    ).fetchone()["s"]
    realized_pnl = conn.execute(
        "SELECT COALESCE(SUM(payout_usd - stake_usd), 0) p FROM demo_bets WHERE mode = ? AND resolved = 1", (mode,)
    ).fetchone()["p"]
    return STARTING_BALANCE - open_stake + realized_pnl


def place_bet(mode, condition_id, clob_token_id, market_question, slug, outcome_name, entry_price, stake_usd):
    if mode not in VALID_MODES:
        return {"ok": False, "error": "Invalid portfolio."}
    try:
        entry_price = float(entry_price)
        stake_usd = float(stake_usd)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid price or stake."}
    if stake_usd <= 0:
        return {"ok": False, "error": "Stake must be positive."}
    if not (0 < entry_price < 1):
        return {"ok": False, "error": "Invalid entry price."}

    with get_conn() as conn:
        cash = _cash_available(conn, mode)
        if stake_usd > cash:
            return {"ok": False, "error": f"Only ${cash:,.2f} available in this demo portfolio."}
        shares = stake_usd / entry_price
        conn.execute(
            """INSERT INTO demo_bets
               (mode, condition_id, clob_token_id, market_question, slug, outcome_name,
                entry_price, stake_usd, shares, placed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mode, condition_id, clob_token_id, market_question, slug, outcome_name,
             entry_price, stake_usd, shares, now_iso()),
        )
    return {"ok": True}


def run_auto_follow(signals):
    """
    Call after every scan. Places one fixed-size auto bet the first time a
    clob_token_id is seen at a qualifying strength, best-score-first, capped
    at MAX_NEW_AUTO_BETS_PER_RUN so the portfolio fills in gradually across
    scans instead of all at once. Never re-bets the same outcome twice,
    regardless of how its strength moves afterward. Signals not reached this
    run aren't marked "seen" -- they're still candidates next run.
    """
    with get_conn() as conn:
        already = {
            r["clob_token_id"]
            for r in conn.execute("SELECT DISTINCT clob_token_id FROM demo_bets WHERE mode = 'auto'")
        }

    candidates = sorted(
        (s for s in signals if s.get("signal_strength") in AUTO_STRENGTH_QUALIFYING
         and s["clob_token_id"] not in already),
        key=lambda s: s.get("score", 0),
        reverse=True,
    )

    placed = 0
    skipped_funds = 0
    for s in candidates:
        if placed >= MAX_NEW_AUTO_BETS_PER_RUN:
            break
        result = place_bet(
            "auto", s["condition_id"], s["clob_token_id"], s["market"], s["slug"],
            s["outcome"], s["implied_probability"], AUTO_STAKE_USD,
        )
        if result["ok"]:
            placed += 1
        else:
            skipped_funds += 1
    return {"placed": placed, "skipped_insufficient_funds": skipped_funds}


def resolve_demo_bets():
    """
    Settle open demo bets whose underlying market has resolved. Piggybacks
    on flagged_markets.resolved (kept current by
    resolution_tracker.check_resolutions) rather than hitting the API again.
    """
    with get_conn() as conn:
        open_bets = [dict(r) for r in conn.execute("SELECT * FROM demo_bets WHERE resolved = 0")]
        if not open_bets:
            return {"settled": 0}

        token_ids = list({b["clob_token_id"] for b in open_bets})
        placeholders = ",".join("?" for _ in token_ids)
        resolved_lookup = {
            r["clob_token_id"]: r["resolved_won"]
            for r in conn.execute(
                f"SELECT clob_token_id, resolved_won FROM flagged_markets "
                f"WHERE resolved = 1 AND clob_token_id IN ({placeholders})",
                tuple(token_ids),
            )
        }

        settled = 0
        resolved_at = now_iso()
        for b in open_bets:
            won = resolved_lookup.get(b["clob_token_id"])
            if won is None:
                continue
            payout = b["shares"] if won else 0.0
            conn.execute(
                "UPDATE demo_bets SET resolved = 1, resolved_won = ?, payout_usd = ?, resolved_at = ? WHERE id = ?",
                (won, round(payout, 2), resolved_at, b["id"]),
            )
            settled += 1
    return {"settled": settled}


def get_portfolio(mode, open_limit=50, resolved_limit=25):
    if mode not in VALID_MODES:
        return None
    with get_conn() as conn:
        cash = _cash_available(conn, mode)
        open_stake = conn.execute(
            "SELECT COALESCE(SUM(stake_usd), 0) s FROM demo_bets WHERE mode = ? AND resolved = 0", (mode,)
        ).fetchone()["s"]
        realized_pnl = conn.execute(
            "SELECT COALESCE(SUM(payout_usd - stake_usd), 0) p FROM demo_bets WHERE mode = ? AND resolved = 1", (mode,)
        ).fetchone()["p"]
        resolved_count = conn.execute(
            "SELECT COUNT(*) c FROM demo_bets WHERE mode = ? AND resolved = 1", (mode,)
        ).fetchone()["c"]
        win_count = conn.execute(
            "SELECT COUNT(*) c FROM demo_bets WHERE mode = ? AND resolved = 1 AND resolved_won = 1", (mode,)
        ).fetchone()["c"]
        open_positions = [
            dict(r) for r in conn.execute(
                "SELECT * FROM demo_bets WHERE mode = ? AND resolved = 0 ORDER BY placed_at DESC LIMIT ?",
                (mode, open_limit),
            )
        ]
        resolved_positions = [
            dict(r) for r in conn.execute(
                "SELECT * FROM demo_bets WHERE mode = ? AND resolved = 1 ORDER BY resolved_at DESC LIMIT ?",
                (mode, resolved_limit),
            )
        ]

    return {
        "mode": mode,
        "starting_balance": STARTING_BALANCE,
        "cash_available": round(cash, 2),
        "open_stake_total": round(open_stake, 2),
        "net_worth_at_cost": round(STARTING_BALANCE + realized_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "resolved_count": resolved_count,
        "win_count": win_count,
        "win_rate": round(win_count / resolved_count, 4) if resolved_count else None,
        "open_positions": open_positions,
        "resolved_positions": resolved_positions,
        "auto_stake_usd": AUTO_STAKE_USD if mode == "auto" else None,
        "auto_strength_qualifying": list(AUTO_STRENGTH_QUALIFYING) if mode == "auto" else None,
    }
