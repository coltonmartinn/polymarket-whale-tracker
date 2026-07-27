"""
Local dashboard for the Polymarket whale-signal scanner.

Run with: python app.py, then open http://127.0.0.1:5000
"""

from flask import Flask, jsonify, render_template, request

from analyzer import build_signals

app = Flask(__name__)

_last_result = {"signals": [], "meta": None}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan")
def api_scan():
    try:
        price_threshold = float(request.args.get("price_threshold", 0.95))
        min_whale_usd = float(request.args.get("min_whale_usd", 1000))
        max_markets = int(request.args.get("max_markets", 200))
        holders_per_market = int(request.args.get("holders_per_market", 10))
    except ValueError:
        return jsonify({"error": "invalid query parameters"}), 400

    price_threshold = min(max(price_threshold, 0.5), 0.999)
    max_markets = min(max(max_markets, 10), 500)
    holders_per_market = min(max(holders_per_market, 1), 25)

    signals, meta = build_signals(
        price_threshold=price_threshold,
        min_whale_usd=min_whale_usd,
        holders_per_market=holders_per_market,
        max_markets=max_markets,
    )
    _last_result["signals"] = signals
    _last_result["meta"] = meta
    return jsonify({"signals": signals, "meta": meta})


@app.route("/api/last")
def api_last():
    return jsonify(_last_result)


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
