"""
Human-reviewed market mapping between Polymarket Global (where our whale
data lives) and Polymarket US (the separate, CFTC-regulated venue actually
tradeable by US residents). There is no shared ID between the two venues,
so this can only produce *candidates* -- ranked by question-text similarity
and end-date proximity -- for a person to confirm or reject by hand. It
never asserts a match on its own: a wrong auto-match here would silently
poison anything built on top of it later, which is exactly the risk the
README flags as the reason execution logic waits on this being resolved.

Confirmed mappings persist in the market_mappings table so a market only
needs to be reviewed once.
"""

import re
from datetime import datetime
from difflib import SequenceMatcher

import polymarket_us_client as pm_us
from db import get_conn, now_iso

DATE_WINDOW_DAYS = 21  # candidate end dates must fall within this many days of each other
# Verified live (2026-07-27): Global questions are full sentences ("Will
# Republicans win the House in the 2026 midterms?") while US titles are
# compact noun phrases ("U.S House Midterm Winner") -- genuinely matching
# pairs score 0.24-0.35 on raw token overlap, well below what a naive
# threshold would treat as a match. 0.5 was tuned against that mistaken
# assumption; lowered after checking real pairs, with the date-proximity
# check (above) as the safety net against a looser text bar.
MIN_TEXT_SIMILARITY = 0.32
MAX_CANDIDATES_PER_MARKET = 5

_STOPWORDS = {
    "will", "the", "a", "an", "to", "in", "on", "of", "for", "by", "be", "is", "at", "and", "or",
    # Verified live (2026-07-27): nearly every Global question ends "...by
    # end of 2026" or similar -- without dropping this boilerplate, two
    # completely unrelated questions ("Will China invade Taiwan..." vs "Who
    # will Trump meet with...") scored 0.34 similarity purely off sharing
    # "end"/"2026", nearly clearing the threshold. Real date matching is
    # already handled separately via endDate proximity (date_gap_days), so
    # a year mentioned in the text itself is redundant here, not signal.
    "end", "within", "before", "after", "during", "year",
}


def _normalize_tokens(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    # Crude stemming so "midterm"/"midterms", "election"/"elections" etc.
    # count as the same token -- without this, plurals alone can sink an
    # otherwise-strong match. Drops 1-2 char fragments (e.g. "U.S."
    # splitting into stray "u"/"s" tokens after punctuation stripping) and
    # bare 4-digit years (see _STOPWORDS note above) -- neither is signal.
    return [
        t[:-1] if t.endswith("s") and len(t) > 4 else t
        for t in tokens
        if len(t) > 2 and not (len(t) == 4 and t.isdigit())
    ]


def _text_similarity(a, b):
    tokens_a = _normalize_tokens(a)
    tokens_b = _normalize_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    jaccard = len(set_a & set_b) / len(set_a | set_b)
    ratio = SequenceMatcher(None, " ".join(tokens_a), " ".join(tokens_b)).ratio()
    return round((jaccard + ratio) / 2, 4)


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_candidates(global_question, global_end_date, us_markets, top_n=MAX_CANDIDATES_PER_MARKET):
    """Rank us_markets by similarity to one Global market; best-first, capped at top_n."""
    g_end = _parse_date(global_end_date)
    scored = []
    for m in us_markets:
        sim = _text_similarity(global_question, m.get("question") or m.get("title") or "")
        if sim < MIN_TEXT_SIMILARITY:
            continue
        m_end = _parse_date(m.get("endDate"))
        date_gap_days = None
        if g_end and m_end:
            date_gap_days = abs((m_end - g_end).days)
            if date_gap_days > DATE_WINDOW_DAYS:
                continue
        scored.append({
            "us_market_id": m.get("id"),
            "us_question": m.get("question") or m.get("title"),
            "us_slug": m.get("slug"),
            "us_end_date": m.get("endDate"),
            "us_category": m.get("category"),
            "similarity": sim,
            "date_gap_days": date_gap_days,
        })
    scored.sort(key=lambda c: c["similarity"], reverse=True)
    return scored[:top_n]


def get_confirmed_mapping(condition_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_mappings WHERE condition_id = ?", (condition_id,)
        ).fetchone()
    return dict(row) if row else None


def confirm_mapping(condition_id, global_question, global_slug, us_market_id, us_question, us_slug, similarity):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO market_mappings
               (condition_id, global_question, global_slug, us_market_id, us_question, us_slug, similarity, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(condition_id) DO UPDATE SET
                 global_question = excluded.global_question,
                 global_slug = excluded.global_slug,
                 us_market_id = excluded.us_market_id,
                 us_question = excluded.us_question,
                 us_slug = excluded.us_slug,
                 similarity = excluded.similarity,
                 confirmed_at = excluded.confirmed_at""",
            (condition_id, global_question, global_slug, us_market_id, us_question, us_slug,
             similarity, now_iso()),
        )


def clear_mapping(condition_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM market_mappings WHERE condition_id = ?", (condition_id,))


def build_mapping_candidates(global_signals, max_us_markets=3000):
    """
    For each distinct Global market in global_signals (as returned by
    analyzer.build_signals -- multiple outcomes/whales can share one
    condition_id), find candidate US matches. Markets with an already-
    confirmed mapping report that instead of re-searching; markets with no
    qualifying candidate still come back with candidates: [] so the caller
    can show "no US equivalent found" explicitly rather than dropping it.
    """
    seen_conditions = {}
    for s in global_signals:
        seen_conditions.setdefault(s["condition_id"], s)

    us_markets = pm_us.get_active_markets(max_markets=max_us_markets)

    results = []
    for s in seen_conditions.values():
        confirmed = get_confirmed_mapping(s["condition_id"])
        candidates = [] if confirmed else find_candidates(s["market"], s.get("end_date"), us_markets)
        results.append({
            "condition_id": s["condition_id"],
            "global_question": s["market"],
            "global_slug": s["slug"],
            "global_url": s.get("polymarket_url"),
            "confirmed": confirmed,
            "candidates": candidates,
        })
    return results
