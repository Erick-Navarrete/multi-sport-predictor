"""Multi-Sport Predictor Web App — Flask backend.

Serves the SPA frontend and provides REST API endpoints
per sport/league from JSON data files.
"""

from flask import Flask, render_template, jsonify
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sports_config import SPORTS, DEFAULT_SPORT, DEFAULT_LEAGUE, get_league_config

app = Flask(__name__)
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
REFRESH_SCRIPT = ROOT_DIR / "refresh_data.py"


def load_league_json(sport, league, name):
    """Load a JSON data file for a specific sport/league."""
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
    """Return all available sports and leagues for the frontend switcher."""
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
    """Return model configuration for a sport/league."""
    from sports_config import get_elo_config, get_sport_config
    try:
        elo_cfg = get_elo_config(sport)
        sport_cfg = get_sport_config(sport)
        league_cfg = get_league_config(sport, league)
    except KeyError:
        return jsonify({"success": False, "message": "Unknown sport/league"}), 404

    return jsonify({"success": True, "data": {
        "ensemble": {"type": "ELO (sport-adaptive)", "estimators": 1},
        "elo": {
            "k": elo_cfg["k"],
            "home_advantage": elo_cfg["home_advantage"],
            "draw_margin": elo_cfg["draw_margin"],
        },
        "blend": {"type": "ELO-only", "ml_weight": 0, "note": "ML ensemble planned"},
        "features": ["elo_a", "elo_b", "elo_diff", "home_advantage"],
        "feature_count": 4,
        "sport": sport,
        "league": league,
        "league_label": league_cfg["label"],
        "result_labels": sport_cfg["result_labels"],
        "competition_type": league_cfg["type"],
        "performance": load_league_json(sport, league, "summary"),
    }})


# ── Refresh API ────────────────────────────────────────────────────

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Trigger a data refresh for all sports."""
    try:
        result = subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": "Data refreshed successfully"})
        return jsonify({
            "success": False,
            "message": f"Refresh failed: {result.stderr[-500:]}",
        }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Refresh timed out"}), 504
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/refresh/<sport>/<league>", methods=["POST"])
def api_refresh_league(sport, league):
    """Trigger a data refresh for a specific sport/league."""
    try:
        result = subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT), sport, league],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": f"{sport}/{league} refreshed successfully"})
        return jsonify({
            "success": False,
            "message": f"Refresh failed: {result.stderr[-500:]}",
        }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "message": "Refresh timed out"}), 504
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
