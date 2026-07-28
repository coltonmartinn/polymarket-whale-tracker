# Polymarket Whale Signal Scanner

Finds near-certain markets on [Polymarket Global](https://polymarket.com) (outcomes priced &ge;95% or &le;5%) and surfaces which large wallets ("whales") are holding real money in them, using Polymarket's public, no-auth Gamma and Data APIs.

**This is informational only, not financial advice.** "Implied probability" is the market's own price, not a prediction this tool makes. What it adds is visibility into where whale money sits relative to that price -- whether whales are confirming the consensus favorite or making a contrarian bet on a longshot.

## Why this exists

Started as an idea to copy top-earning traders on Kalshi; dropped because Kalshi's trade feed is anonymized. Polymarket Global is fully on-chain -- every trade is tied to a public wallet -- so wallet-level whale tracking is actually possible there.

Note: Polymarket Global is geo-blocked to US persons for trading. Polymarket US (QCX LLC) is the separate, CFTC-regulated venue for US residents, but it lacks Global's wallet-level transparency. The intent here is to treat Global whale activity as a read-only research signal, not to trade on Global directly.

## Project layout

- `polymarket_client.py` -- thin wrapper around the Gamma (`gamma-api.polymarket.com`), Data (`data-api.polymarket.com`), and CLOB (`clob.polymarket.com`, price history only) APIs.
- `analyzer.py` -- scans active markets, flags near-certain outcomes, pulls whale holders, computes USD exposure (shares &times; price) and a signal-strength score.
- `db.py` -- shared SQLite store (`whale_tracker.db`, gitignored): every scan's whale snapshots, flagged-market resolution status, and calibration observations.
- `momentum.py` -- persists each scan and diffs it against the previous one to detect whales entering, adding to, reducing, or exiting near-certain positions.
- `backtest.py` -- historical calibration backtest: samples resolved binary markets, pulls their final-15-days price history, and checks whether near-certain prices were actually right.
- `resolution_tracker.py` -- checks previously-flagged markets for resolution and records whether the whale-backed outcome won, building a live/forward version of the calibration check from our own scans. Also tags every wallet that held the resolved position, which feeds the whale leaderboard: per-wallet win rate across their resolved calls, ranked by a confidence-adjusted (Wilson lower bound) score rather than raw win rate, so a small sample can't outrank a proven one.
- `scheduled_scan.py` -- entry point for the recurring background scan (see Automation below).
- `app.py` -- Flask app serving the dashboard and its API endpoints.
- `polymarket_us_client.py` -- thin client for Polymarket US's public, no-auth Gateway API (`gateway.polymarket.us`) -- the separate, CFTC-regulated venue (QCX LLC) actually tradeable by US residents. As of writing its catalog is heavily sports-skewed (~87% of active markets in a 500-market sample), which is why callers default to non-sports categories only.
- `market_mapper.py` -- human-reviewed mapping between Polymarket Global (where whale data lives) and Polymarket US (the tradeable venue). No shared IDs exist between the two, so this surfaces ranked candidate matches (question-text similarity + end-date proximity) for manual confirm/reject rather than auto-linking; confirmed mappings persist.
- `demo_portfolio.py` -- two fake-money, $10,000-start paper-trading portfolios (no real funds involved). Manual bets are placed by hand from the Scanner tab; Auto-follow mechanically bets a fixed $250 on the first time any signal reaches High/Very High strength, capped at 5 new bets per scan so one scan's 100+ qualifying signals can't spend the whole balance at once. Positions settle by piggybacking on `flagged_markets.resolved` -- no extra API calls.
- `templates/`, `static/` -- the dashboard UI (six tabs: Scanner, Momentum, Calibration Backtest, Live Track Record -- which also includes the whale leaderboard -- Market Mapping, and Demo Portfolio).
- `collector.py` -- standalone CLI script that does a one-off scan and writes `whale_positions.csv`, independent of the DB/dashboard.

## Setup

```
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

For a one-off CSV export instead of the UI:

```
python collector.py
```

## How signal strength is scored

For each near-certain outcome, whale positions above a configurable USD threshold are summed. The score weights both absolute whale dollars and what fraction of the market's visible order-book liquidity those wallets represent, so a large position in a thin market ranks higher than the same dollar amount in a deep one. Buckets: Low / Medium / High / Very High.

## Does "near-certain" actually mean anything?

The Calibration Backtest tab answers this independently of whale data: across 108 resolved binary markets (216 distinct near-certain calls, deduplicated to avoid overcounting markets that sat near-certain for weeks), favorites priced &ge;95% resolved correctly **96.3%** of the time and longshots priced &le;5% hit **3.7%** of the time -- both close to calibrated, Brier score 0.036. Re-run it any time from the dashboard; methodology and known scope limits (binary markets only, final 15 days before resolution, volume-filtered) are documented in `backtest.py`'s module docstring.

## Automation

A Windows Task Scheduler job (`PolymarketWhaleScanner`) runs `scheduled_scan.py` every 30 minutes: it scans, persists the snapshot, diffs it against the previous scan (feeding the Momentum tab), and checks previously-flagged markets for resolution (feeding the Live Track Record tab and settling any resolved demo bets). Logs to `scheduled_scan.log`. Recreate it with:

```
$action = New-ScheduledTaskAction -Execute "<path to python.exe>" -Argument "scheduled_scan.py" -WorkingDirectory "<repo path>"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "PolymarketWhaleScanner" -Action $action -Trigger $trigger
```

A second Task Scheduler job (`PolymarketWhaleDashboard`) starts `app.py` at logon, since it's otherwise just a manually-started background process that doesn't survive a reboot/logoff -- the dashboard being unreachable ("Failed to fetch" on every request) usually means this. Check it with `Get-ScheduledTask -TaskName PolymarketWhaleDashboard | Get-ScheduledTaskInfo`, or recreate it with:

```
$action = New-ScheduledTaskAction -Execute "<path to python.exe>" -Argument "app.py" -WorkingDirectory "<repo path>"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "<username>"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "<username>" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "PolymarketWhaleDashboard" -Action $action -Trigger $trigger -Settings $settings -Principal $principal
# Then, since New-ScheduledTaskSettingsSet has no switch to negate these, flip them off directly so the task isn't stopped/blocked on battery power:
$task = Get-ScheduledTask -TaskName "PolymarketWhaleDashboard"
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
Set-ScheduledTask -TaskName "PolymarketWhaleDashboard" -Settings $task.Settings
```

## Whale leaderboard

Every resolved market rolls each holding wallet's outcome into `wallet_calls`. The Live Track Record tab's leaderboard aggregates that per-wallet, requires at least 3 resolved calls to qualify (fewer than that is noise, not signal), and ranks by a 95%-confidence Wilson lower bound rather than raw win rate -- a wallet that's 1-for-1 should not outrank one that's 20-for-25. Starts empty, like the rest of live tracking; grows as flagged markets resolve.

## Global &harr; US market mapping

Polymarket US has a genuine public API (`gateway.polymarket.us`, no auth required) with a market shape similar to Global's, but a completely separate catalog and no shared IDs -- and as of writing, the catalog is dominated by sports (~87% of a 500-market sample), with only politics/macro/culture as realistic overlap candidates with what the scanner flags. Because a wrong auto-match would silently poison anything built on top of it, the Market Mapping tab never auto-links: it surfaces ranked candidates (question-text similarity + end-date proximity, from your most recent scan) for you to confirm or reject by hand. Confirmed links persist and don't need re-reviewing.

## Demo portfolio

Two independent fake-money portfolios ($10,000 starting balance each, no real funds) let you sanity-check betting behavior against live outcomes. **Manual**: click "Demo bet" on any card in the Scanner tab, choose a stake, and it's tracked until that market resolves. **Auto-follow**: mechanically places a fixed $250 bet the first time any signal reaches High/Very High strength -- a test of "would blindly following strong whale signals have made money" -- capped at 5 new bets per scan (best-scoring first) since a single scan can flag 100+ qualifying signals and would otherwise spend the whole balance in one shot. Positions are valued at cost, not marked-to-market, so nothing displayed claims an unrealized gain from a price that could still reverse before resolution.

## Roadmap

- Let the live track record and whale leaderboard accumulate (both start empty by design) and compare whale-backed vs non-whale-backed near-certain markets' resolution accuracy, and individual whale win rates, once there's enough sample size.
- Build execution logic against confirmed Global&harr;US mappings, once enough of them exist to be useful.
