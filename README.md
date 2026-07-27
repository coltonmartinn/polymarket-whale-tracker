# Polymarket Whale Signal Scanner

Finds near-certain markets on [Polymarket Global](https://polymarket.com) (outcomes priced &ge;95% or &le;5%) and surfaces which large wallets ("whales") are holding real money in them, using Polymarket's public, no-auth Gamma and Data APIs.

**This is informational only, not financial advice.** "Implied probability" is the market's own price, not a prediction this tool makes. What it adds is visibility into where whale money sits relative to that price -- whether whales are confirming the consensus favorite or making a contrarian bet on a longshot.

## Why this exists

Started as an idea to copy top-earning traders on Kalshi; dropped because Kalshi's trade feed is anonymized. Polymarket Global is fully on-chain -- every trade is tied to a public wallet -- so wallet-level whale tracking is actually possible there.

Note: Polymarket Global is geo-blocked to US persons for trading. Polymarket US (QCX LLC) is the separate, CFTC-regulated venue for US residents, but it lacks Global's wallet-level transparency. The intent here is to treat Global whale activity as a read-only research signal, not to trade on Global directly.

## Project layout

- `polymarket_client.py` -- thin wrapper around the Gamma (`gamma-api.polymarket.com`) and Data (`data-api.polymarket.com`) APIs.
- `analyzer.py` -- scans active markets, flags near-certain outcomes, pulls whale holders, computes USD exposure (shares &times; price) and a signal-strength score.
- `app.py` -- Flask app serving the dashboard and a `/api/scan` endpoint.
- `templates/`, `static/` -- the dashboard UI.
- `collector.py` -- standalone CLI script that does the same scan and writes `whale_positions.csv`, for scripted/scheduled runs without the UI.

## Setup

```
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 and click **Scan Market**.

For a one-off CSV export instead of the UI:

```
python collector.py
```

## How signal strength is scored

For each near-certain outcome, whale positions above a configurable USD threshold are summed. The score weights both absolute whale dollars and what fraction of the market's visible order-book liquidity those wallets represent, so a large position in a thin market ranks higher than the same dollar amount in a deep one. Buckets: Low / Medium / High / Very High.

## Roadmap

- Snapshot-diffing across scans to detect whales *adding* to positions over time (stronger signal than a single snapshot).
- Resolve market-mapping between Polymarket Global and Polymarket US before any execution logic is considered.
