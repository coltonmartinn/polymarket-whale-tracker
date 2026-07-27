"""
SQLite store shared by the live scanner (snapshots for diffing) and the
calibration backtest / live track record (resolved-market observations).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "whale_tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    price_threshold REAL,
    min_whale_usd REAL,
    markets_scanned INTEGER,
    markets_flagged INTEGER
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    condition_id TEXT NOT NULL,
    clob_token_id TEXT NOT NULL,
    market_question TEXT,
    slug TEXT,
    outcome_name TEXT,
    outcome_type TEXT,
    price REAL,
    liquidity REAL,
    volume REAL,
    wallet TEXT NOT NULL,
    wallet_name TEXT,
    shares REAL,
    usd_value REAL,
    scanned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_wallet_outcome ON snapshots(clob_token_id, wallet);
CREATE INDEX IF NOT EXISTS idx_snapshots_scan ON snapshots(scan_id);

CREATE TABLE IF NOT EXISTS flagged_markets (
    clob_token_id TEXT PRIMARY KEY,
    condition_id TEXT,
    question TEXT,
    slug TEXT,
    outcome_name TEXT,
    first_flagged_at TEXT,
    last_seen_at TEXT,
    first_flagged_price REAL,
    had_whale_momentum INTEGER DEFAULT 0,
    resolved INTEGER DEFAULT 0,
    resolved_won INTEGER,
    resolved_at TEXT,
    last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS calibration_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT,
    question TEXT,
    outcome_name TEXT,
    price REAL,
    days_before_resolution REAL,
    won INTEGER,
    observed_at TEXT,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_calib_condition ON calibration_observations(condition_id);

CREATE TABLE IF NOT EXISTS calibration_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    markets_sampled INTEGER,
    markets_used INTEGER,
    observations INTEGER
);

CREATE TABLE IF NOT EXISTS wallet_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT NOT NULL,
    wallet_name TEXT,
    clob_token_id TEXT NOT NULL,
    condition_id TEXT,
    question TEXT,
    outcome_name TEXT,
    usd_value REAL,
    won INTEGER,
    resolved_at TEXT,
    UNIQUE(wallet, clob_token_id)
);
CREATE INDEX IF NOT EXISTS idx_wallet_calls_wallet ON wallet_calls(wallet);

CREATE TABLE IF NOT EXISTS market_mappings (
    condition_id TEXT PRIMARY KEY,
    global_question TEXT,
    global_slug TEXT,
    us_market_id TEXT,
    us_question TEXT,
    us_slug TEXT,
    similarity REAL,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS demo_bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL CHECK(mode IN ('manual', 'auto')),
    condition_id TEXT NOT NULL,
    clob_token_id TEXT NOT NULL,
    market_question TEXT,
    slug TEXT,
    outcome_name TEXT,
    entry_price REAL NOT NULL,
    stake_usd REAL NOT NULL,
    shares REAL NOT NULL,
    placed_at TEXT NOT NULL,
    resolved INTEGER DEFAULT 0,
    resolved_won INTEGER,
    payout_usd REAL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_demo_bets_mode ON demo_bets(mode, resolved);
CREATE INDEX IF NOT EXISTS idx_demo_bets_token ON demo_bets(clob_token_id, mode);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


init_db()
