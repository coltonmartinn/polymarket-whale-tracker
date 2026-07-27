"""
Local dashboard for the Polymarket whale-signal scanner.

Run with: python app.py, then open http://127.0.0.1:5000
"""

from flask import Flask, jsonify, render_template, request

from analyzer import build_signals
from backtest import run_backtest, get_calibration_summary
from momentum import record_scan, compute_momentum, latest_momentum
from resolution_tracker import check_resolutions, get_tracking_status

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

    scan_id = record_scan(signals, meta)
    momentum = compute_momentum(scan_id)

    return jsonify({"signals": signals, "meta": meta, "momentum": momentum})


@app.route("/api/last")
def api_last():
    return jsonify(_last_result)


@app.route("/api/momentum")
def api_momentum():
    """Diff the two most recent persisted scans without running a new live scan."""
    return jsonify(latest_momentum())


@app.route("/api/calibration")
def api_calibration():
    return jsonify(get_calibration_summary(source="historical_backtest"))


@app.route("/api/calibration/run", methods=["POST"])
def api_calibration_run():
    try:
        max_markets = int(request.args.get("max_markets", 250))
        min_volume = float(request.args.get("min_volume", 5000))
    except ValueError:
        return jsonify({"error": "invalid query parameters"}), 400

    max_markets = min(max(max_markets, 10), 600)
    result = run_backtest(max_markets=max_markets, min_volume=min_volume)
    return jsonify({**result, "summary": get_calibration_summary(source="historical_backtest")})


@app.route("/api/track_record")
def api_track_record():
    return jsonify({
        **get_calibration_summary(source="live_tracking"),
        "tracking_status": get_tracking_status(),
    })


@app.route("/api/track_record/refresh", methods=["POST"])
def api_track_record_refresh():
    result = check_resolutions()
    return jsonify({**result, "summary": get_calibration_summary(source="live_tracking")})


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
