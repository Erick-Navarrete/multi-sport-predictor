"""Multi-Sport Predictor Web App — Flask backend.

Serves the SPA frontend and provides REST API endpoints
per sport/league from JSON data files.
"""

from flask import Flask, render_template, jsonify
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sports_config import (
    SPORTS, DEFAULT_SPORT, DEFAULT_LEAGUE, get_league_config,
    get_elo_config, get_sport_config, get_blend_weights, get_odds_sport_key, ODDS_API_KEY,
    list_all_leagues,
)

app = Flask(__name__)
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
REFRESH_SCRIPT = ROOT_DIR / "refresh_data.py"


def load_league_json(sport, league, name):
    p = DATA_DIR / sport / league / f"{name}.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return [] if name != "summary" else {}


# ── Frontend ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Sport/League Discovery API ─────────────────────────────────────

@app.route("/api/sports")
def api_sports():
    result = []
    for sport_key, sport in SPORTS.items():
        leagues = []
        for league_key, league in sport["leagues"].items():
            leagues.append({
                "slug": league_key,
                "label": league["label"],
                "country": league.get("country", ""),
                "has_data": (DATA_DIR / sport_key / league_key / "summary.json").exists(),
            })
        result.append({
            "slug": sport_key,
            "label": sport["label"],
            "icon": sport.get("icon", ""),
            "leagues": leagues,
        })
    return jsonify({"success": True, "data": result, "default_sport": DEFAULT_SPORT, "default_league": DEFAULT_LEAGUE})


# ── Home Page API ──────────────────────────────────────────────────

@app.route("/api/home/weekly-picks")
def api_weekly_picks():
    """Aggregate top-confidence picks across all leagues for the current week.

    Returns picks sorted by confidence with $100 bankroll allocation.
    Bet sizing: proportional to confidence (Kelly-lite), scaled so total = $100.
    """
    this_week = datetime.now(timezone.utc).isocalendar()[1]
    this_year = datetime.now(timezone.utc).year
    all_picks = []

    for sport, league, label in list_all_leagues():
        summary = load_league_json(sport, league, "summary")
        if summary.get("season_status") in ("dormant",):
            continue

        predictions = load_league_json(sport, league, "predictions")
        for p in predictions:
            if p.get("week_number") == this_week and p.get("year") == this_year:
                if p.get("confidence_level") in ("High", "Medium"):
                    all_picks.append({
                        "sport": sport,
                        "league": label,
                        "date": p.get("date", ""),
                        "entity_a": p.get("entity_a", ""),
                        "entity_b": p.get("entity_b", ""),
                        "prediction": p.get("prediction", ""),
                        "confidence": p.get("confidence", 0),
                        "confidence_level": p.get("confidence_level", "Low"),
                        "a_win_prob": p.get("a_win_prob", 0),
                        "draw_prob": p.get("draw_prob", 0),
                        "b_win_prob": p.get("b_win_prob", 0),
                        "elo_diff": p.get("elo_diff", 0),
                        "sources": p.get("sources", {}),
                        "odds_available": p.get("odds_available", False),
                    })

    # Sort by confidence descending
    all_picks.sort(key=lambda p: p["confidence"], reverse=True)

    # Cap at top 15 picks max
    all_picks = all_picks[:15]

    # Allocate $100 bankroll: weight by confidence, higher confidence = bigger bet
    # Simple model: each pick gets a share proportional to (confidence - 40)
    # This means sub-40% picks get nothing, and the rest share $100 proportionally
    raw_weights = []
    for p in all_picks:
        raw = max(p["confidence"] - 40, 0)
        raw_weights.append(raw)

    total_raw = sum(raw_weights) or 1
    total_bankroll = 100.0

    for i, p in enumerate(all_picks):
        share = raw_weights[i] / total_raw
        bet_amount = round(share * total_bankroll, 2)
        p["bet_amount"] = bet_amount
        # Estimated payout: if prediction is correct, return = bet * (100 / predicted_prob)
        # This approximates a "fair odds" payout
        predicted_prob = max(p["a_win_prob"], p["b_win_prob"], p["draw_prob"])
        if predicted_prob > 0 and predicted_prob < 100:
            p["estimated_payout"] = round(bet_amount * (100 / predicted_prob), 2)
            p["estimated_profit"] = round(p["estimated_payout"] - bet_amount, 2)
        else:
            p["estimated_payout"] = bet_amount
            p["estimated_profit"] = 0

    # Aggregate ROI from historical data across all leagues
    total_correct = 0
    total_historical = 0
    league_accuracy = []
    for sport, league, label in list_all_leagues():
        summary = load_league_json(sport, league, "summary")
        acc = summary.get("accuracy", 0)
        total = summary.get("total_historical", 0)
        correct = summary.get("correct", 0)
        total_correct += correct
        total_historical += total
        if total > 0:
            league_accuracy.append({"league": label, "accuracy": acc, "matches": total})

    overall_accuracy = round(total_correct / total_historical * 100, 1) if total_historical else 0

    return jsonify({"success": True, "data": {
        "week": this_week,
        "year": this_year,
        "bankroll": 100.0,
        "picks": all_picks,
        "total_picks": len(all_picks),
        "high_confidence_count": sum(1 for p in all_picks if p["confidence_level"] == "High"),
        "model_accuracy": overall_accuracy,
        "total_historical": total_historical,
        "league_accuracy": league_accuracy,
    }})


# ── Per-Sport/League Data API ──────────────────────────────────────

@app.route("/api/<sport>/<league>/summary")
def api_summary(sport, league):
    return jsonify({"success": True, "data": load_league_json(sport, league, "summary")})


@app.route("/api/<sport>/<league>/predictions")
def api_predictions(sport, league):
    data = load_league_json(sport, league, "predictions")
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/<sport>/<league>/historical")
def api_historical(sport, league):
    data = load_league_json(sport, league, "historical")
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/<sport>/<league>/standings")
def api_standings(sport, league):
    data = load_league_json(sport, league, "standings")
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/<sport>/<league>/model-info")
def api_model_info(sport, league):
    try:
        elo_cfg = get_elo_config(sport)
        sport_cfg = get_sport_config(sport)
        league_cfg = get_league_config(sport, league)
        blend_weights = get_blend_weights(sport)
    except KeyError:
        return jsonify({"success": False, "message": "Unknown sport/league"}), 404

    odds_key = get_odds_sport_key(sport, league)
    summary = load_league_json(sport, league, "summary")

    return jsonify({"success": True, "data": {
        "ensemble": {
            "type": "Multi-Signal Blend (ELO + Colley + Form + Odds)",
            "estimators": 4,
            "description": "Blends four independent prediction signals with sport-specific weights",
        },
        "elo": {
            "k": elo_cfg["k"],
            "home_advantage": elo_cfg["home_advantage"],
            "draw_margin": elo_cfg["draw_margin"],
            "decay": 0.98,
            "decay_note": "Recent matches weighted heavier (0.98 decay factor per match)",
        },
        "colley": {
            "type": "Colley Matrix",
            "description": "Strength-of-schedule-adjusted ratings via linear algebra (C·r=b)",
            "uses_margin": False,
            "note": "Independent from ELO — pure win/loss with SOS adjustment",
        },
        "form": {
            "type": "Recent Form Signal",
            "window": 5,
            "description": "Last 5 matches weighted recency-adjusted (0-100 scale)",
        },
        "odds": {
            "provider": "The-Odds-API" if ODDS_API_KEY else "Not configured",
            "configured": bool(ODDS_API_KEY),
            "sport_key": odds_key,
            "coverage": "US bookmakers (h2h/moneyline)",
            "fallback": "Odds weight redistributed to ELO/Colley/Form when unavailable",
        },
        "blend": {
            "type": "Weighted Blend",
            "weights": blend_weights,
            "note": f"Weights tuned per sport; odds weight={blend_weights['odds']} when available",
        },
        "features": [
            "elo_a", "elo_b", "elo_diff", "elo_decay",
            "colley_a", "colley_b", "colley_diff",
            "form_a", "form_b", "form_diff",
            "odds_implied_a", "odds_implied_b",
            "home_advantage",
        ],
        "feature_count": 13,
        "sport": sport,
        "league": league,
        "league_label": league_cfg["label"],
        "result_labels": sport_cfg["result_labels"],
        "competition_type": league_cfg["type"],
        "performance": summary,
        "season_status": summary.get("season_status", "in_season"),
    }})


# ── Refresh API ────────────────────────────────────────────────────

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        result = subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": "Data refreshed successfully"})
        return jsonify({"success": False, "message": f"Refresh failed: {result.stderr[-500:]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Refresh timed out"}), 504
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/refresh/<sport>/<league>", methods=["POST"])
def api_refresh_league(sport, league):
    try:
        result = subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT), sport, league],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": f"{sport}/{league} refreshed successfully"})
        return jsonify({"success": False, "message": f"Refresh failed: {result.stderr[-500:]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Refresh timed out"}), 504
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
