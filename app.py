"""
Local dashboard for the Polymarket whale-signal scanner.

Run with: python app.py, then open http://127.0.0.1:5000
"""

from flask import Flask, jsonify, render_template, request

from analyzer import build_signals
from backtest import run_backtest, get_calibration_summary
from momentum import record_scan, compute_momentum, latest_momentum, apply_momentum
from resolution_tracker import check_resolutions, get_tracking_status, get_wallet_leaderboard
from market_mapper import build_mapping_candidates, confirm_mapping, clear_mapping
from demo_portfolio import place_bet, run_auto_follow, resolve_demo_bets, get_portfolio

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
    apply_momentum(signals, scan_id)  # attaches per-whale trend + shifts signal_strength in place
    momentum = compute_momentum(scan_id)
    auto_follow = run_auto_follow(signals)  # places demo bets on any newly-qualifying signal

    return jsonify({"signals": signals, "meta": meta, "momentum": momentum, "auto_follow": auto_follow})


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
    resolve_demo_bets()  # settle any demo positions whose market just resolved
    return jsonify({**result, "summary": get_calibration_summary(source="live_tracking")})


@app.route("/api/wallet_leaderboard")
def api_wallet_leaderboard():
    try:
        min_calls = int(request.args.get("min_calls", 3))
    except ValueError:
        return jsonify({"error": "invalid query parameters"}), 400
    min_calls = min(max(min_calls, 1), 50)
    return jsonify(get_wallet_leaderboard(min_calls=min_calls))


@app.route("/api/market_mapping")
def api_market_mapping():
    if not _last_result["signals"]:
        return jsonify({"error": "no_scan", "message": "Run a scan on the Scanner tab first."}), 400
    return jsonify({"markets": build_mapping_candidates(_last_result["signals"])})


@app.route("/api/market_mapping/confirm", methods=["POST"])
def api_market_mapping_confirm():
    body = request.get_json(silent=True) or {}
    condition_id = body.get("condition_id")
    us_market_id = body.get("us_market_id")
    if not condition_id or not us_market_id:
        return jsonify({"error": "missing_fields"}), 400
    confirm_mapping(
        condition_id, body.get("global_question"), body.get("global_slug"),
        us_market_id, body.get("us_question"), body.get("us_slug"), body.get("similarity"),
    )
    return jsonify({"ok": True})


@app.route("/api/market_mapping/clear", methods=["POST"])
def api_market_mapping_clear():
    body = request.get_json(silent=True) or {}
    condition_id = body.get("condition_id")
    if not condition_id:
        return jsonify({"error": "missing_fields"}), 400
    clear_mapping(condition_id)
    return jsonify({"ok": True})


@app.route("/api/demo/<mode>")
def api_demo_portfolio(mode):
    portfolio = get_portfolio(mode)
    if portfolio is None:
        return jsonify({"error": "invalid_mode"}), 404
    return jsonify(portfolio)


@app.route("/api/demo/manual/bet", methods=["POST"])
def api_demo_manual_bet():
    body = request.get_json(silent=True) or {}
    required = ["condition_id", "clob_token_id", "market_question", "outcome_name", "entry_price", "stake_usd"]
    if not all(body.get(k) is not None for k in required):
        return jsonify({"ok": False, "error": "missing_fields"}), 400
    result = place_bet(
        "manual", body["condition_id"], body["clob_token_id"], body["market_question"],
        body.get("slug"), body["outcome_name"], body["entry_price"], body["stake_usd"],
    )
    return jsonify(result), (200 if result["ok"] else 400)


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
