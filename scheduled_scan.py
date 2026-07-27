"""
Entry point for the recurring scan, invoked by Windows Task Scheduler.
Runs a scan, persists it (building the snapshot history momentum.py diffs
against), and checks previously-flagged markets for resolution.

Logs to scheduled_scan.log in the project directory so runs can be audited
without a console attached.
"""

import logging
import os
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    filename="scheduled_scan.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from analyzer import build_signals
from momentum import record_scan, compute_momentum
from resolution_tracker import check_resolutions


def main():
    started = time.time()
    try:
        signals, meta = build_signals(price_threshold=0.95, min_whale_usd=1000, holders_per_market=10, max_markets=200)
        scan_id = record_scan(signals, meta)
        momentum = compute_momentum(scan_id)
        moved = sum(1 for e in momentum["events"] if e["type"] in ("new_entry", "increased"))
        resolutions = check_resolutions()
        logging.info(
            "scan_id=%s markets_scanned=%s signals=%s momentum_events=%s (%s adds/entries) "
            "resolutions_checked=%s newly_resolved=%s elapsed=%.1fs",
            scan_id, meta["markets_scanned"], len(signals), len(momentum["events"]), moved,
            resolutions["checked"], resolutions["newly_resolved"], time.time() - started,
        )
    except Exception:
        logging.exception("scheduled scan failed")
        raise


if __name__ == "__main__":
    main()
