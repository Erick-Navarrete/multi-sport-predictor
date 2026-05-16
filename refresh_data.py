"""Multi-sport prediction data refresh — ESPN API only.

Fetches completed results and upcoming fixtures from ESPN scoreboard,
generates ELO predictions (sport-specific params), and updates JSON
data files per sport/league in data/{sport}/{league}/.
"""

import json
import requests
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sports_config import (
    SPORTS, get_elo_config, get_sport_config, get_league_config,
    list_all_leagues, is_player_vs_player, TEAM_VS_TEAM,
)

ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"


def load_json(path):
    """Load JSON from absolute path."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_json(path, data):
    """Save JSON to absolute path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size = len(data) if isinstance(data, list) else "dict"
    print(f"  Saved {path.relative_to(DATA_DIR)} ({size})")


# ── ESPN API Fetchers ──────────────────────────────────────────────

ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"


def fetch_espn_scoreboard(sport, league, date_str):
    """Fetch ESPN scoreboard for a specific date. Returns parsed JSON or None."""
    url = f"{ESPN_SITE_BASE}/{sport}/{league}/scoreboard?dates={date_str}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ESPN fetch failed for {sport}/{league} on {date_str}: {e}")
    return None


def _extract_competitions(event):
    """Extract competition dicts from an ESPN event.

    Team sports: competitions at event["competitions"]
    Tennis: competitions nested inside event["groupings"][N]["competitions"]
    MMA: competitions at event["competitions"] but use "athlete" instead of "team"
    """
    comps = []
    if event.get("competitions"):
        for comp in event["competitions"]:
            comps.append(comp)
    for g in event.get("groupings", []):
        for comp in g.get("competitions", []):
            comps.append(comp)
    return comps


def _get_entity_name(competitor):
    """Extract entity name from a competitor dict.

    Team sports: competitor["team"]["shortDisplayName"]
    Player sports: competitor["athlete"]["displayName"]
    """
    if "team" in competitor:
        return competitor["team"].get("shortDisplayName") or competitor["team"].get("displayName", "")
    if "athlete" in competitor:
        return competitor["athlete"].get("displayName", "")
    return ""


def parse_espn_competition(comp, sport, league, event_date, event_time):
    """Parse a single ESPN competition into a standardized match dict."""
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None

    entity_a = entity_b = None
    score_a = score_b = 0
    winner = None

    # Sort by order for consistent a/b assignment
    sorted_comps = sorted(competitors, key=lambda c: c.get("order", 99))

    for idx, c in enumerate(sorted_comps):
        name = _get_entity_name(c)
        score = int(c.get("score", 0))
        is_winner = c.get("winner", False)

        # home = entity_a, away = entity_b; fallback to first/second
        if c.get("homeAway") == "home" or (idx == 0 and not c.get("homeAway")):
            entity_a = name
            score_a = score
            if is_winner:
                winner = "a"
        else:
            entity_b = name
            score_b = score
            if is_winner:
                winner = "b"

    if not entity_a or not entity_b:
        return None

    comp_status = comp.get("status", {}).get("type", {})
    state = comp_status.get("state", "pre")

    # Determine result
    cfg = get_sport_config(sport)
    labels = cfg["result_labels"]
    result = None

    if state == "post":
        if winner == "a":
            result = labels["home"] or labels["away"]
        elif winner == "b":
            result = labels["away"] or labels["home"]
        elif score_a == score_b and labels.get("draw"):
            result = labels["draw"]
        if result is None:
            result = labels["home"] if score_a > score_b else labels["away"]

    venue = comp.get("venue", {}).get("fullName", "") or "Unknown"
    comp_date = (comp.get("date", "")[:10]) or event_date
    comp_time = (comp.get("date", "")[11:16]) or event_time or "00:00"

    return {
        "date": comp_date,
        "time": comp_time,
        "entity_a": entity_a,
        "entity_b": entity_b,
        "score_a": score_a,
        "score_b": score_b,
        "status": state,
        "winner": winner,
        "result": result,
        "venue": venue,
    }


def fetch_completed_matches(sport, league, days_back=5):
    """Fetch recently completed matches from ESPN scoreboard."""
    results = []
    today = datetime.now(timezone.utc).date()

    for i in range(days_back):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        data = fetch_espn_scoreboard(sport, league, date_str)
        if not data:
            continue

        for ev in data.get("events", []):
            event_date = (ev.get("date", "")[:10])
            event_time = (ev.get("date", "")[11:16]) or "00:00"

            for comp in _extract_competitions(ev):
                comp_status = comp.get("status", {}).get("type", {})
                if comp_status.get("state") != "post":
                    continue
                match = parse_espn_competition(comp, sport, league, event_date, event_time)
                if match and match["entity_a"] and match["entity_b"]:
                    results.append(match)

    return results


def fetch_upcoming_fixtures(sport, league, days_ahead=10):
    """Fetch upcoming scheduled fixtures from ESPN scoreboard."""
    fixtures = []
    seen = set()
    today = datetime.now(timezone.utc).date()

    for i in range(days_ahead):
        d = today + timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        data = fetch_espn_scoreboard(sport, league, date_str)
        if not data:
            continue

        for ev in data.get("events", []):
            event_date = (ev.get("date", "")[:10])
            event_time = (ev.get("date", "")[11:16]) or "00:00"

            for comp in _extract_competitions(ev):
                comp_status = comp.get("status", {}).get("type", {})
                if comp_status.get("state") != "pre":
                    continue
                match = parse_espn_competition(comp, sport, league, event_date, event_time)
                if not match or not match["entity_a"] or not match["entity_b"]:
                    continue
                key = f"{match['date']}|{match['entity_a']}|{match['entity_b']}"
                if key in seen:
                    continue
                seen.add(key)
                fixtures.append(match)

    return fixtures


# ── ELO Rating System ──────────────────────────────────────────────

class SportELO:
    """Sport-adaptive ELO rating system.

    Supports configurable k-factor, home advantage, and draw margin.
    Works for both team and player sports.
    """

    def __init__(self, k=32, home_advantage=65, draw_margin=8):
        self.k = k
        self.home_advantage = home_advantage
        self.draw_margin = draw_margin
        self.ratings = {}

    def get_rating(self, entity):
        return self.ratings.get(entity, 1500)

    def expected_score(self, ra, rb):
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def predict(self, entity_a, entity_b):
        """Generate prediction probabilities."""
        r_a = self.get_rating(entity_a) + self.home_advantage
        r_b = self.get_rating(entity_b)
        elo_diff = r_a - r_b

        e_a = self.expected_score(r_a, r_b)

        draw_prob = 0.0
        if self.draw_margin > 0:
            draw_prob = max(12.0, 26.0 - abs(elo_diff) * 0.06)
            draw_prob = round(draw_prob, 1)

        remaining = 100.0 - draw_prob
        a_win_prob = round(remaining * e_a, 1)
        b_win_prob = round(100.0 - a_win_prob - draw_prob, 1)

        top = max(a_win_prob, draw_prob, b_win_prob)

        if self.draw_margin > 0 and top - draw_prob < self.draw_margin:
            prediction = "Draw"
        elif a_win_prob >= b_win_prob:
            prediction = "a_win"
        else:
            prediction = "b_win"

        confidence = round(top, 1)
        confidence_level = "High" if confidence >= 70 else "Medium" if confidence >= 50 else "Low"

        return {
            "a_win_prob": a_win_prob,
            "draw_prob": draw_prob,
            "b_win_prob": b_win_prob,
            "prediction": prediction,
            "confidence": confidence,
            "confidence_level": confidence_level,
            "elo_a": self.get_rating(entity_a),
            "elo_b": self.get_rating(entity_b),
            "elo_diff": round(self.get_rating(entity_a) - self.get_rating(entity_b)),
        }

    def update(self, entity_a, entity_b, score_a, score_b):
        """Update ELO ratings after a completed match."""
        r_a = self.get_rating(entity_a) + self.home_advantage
        r_b = self.get_rating(entity_b)

        e_a = self.expected_score(r_a, r_b)
        e_b = 1.0 - e_a

        if score_a > score_b:
            s_a, s_b = 1.0, 0.0
        elif score_a < score_b:
            s_a, s_b = 0.0, 1.0
        else:
            s_a, s_b = 0.5, 0.5

        new_a = self.get_rating(entity_a) + self.k * (s_a - e_a)
        new_b = self.get_rating(entity_b) + self.k * (s_b - e_b)

        self.ratings[entity_a] = round(new_a)
        self.ratings[entity_b] = round(new_b)


# ── Data Pipeline Functions ────────────────────────────────────────

def build_elo_from_history(completed_matches, elo_config):
    """Build ELO ratings by replaying all completed matches."""
    elo = SportELO(**elo_config)
    for m in completed_matches:
        if m.get("score_a") is not None and m.get("score_b") is not None:
            elo.update(m["entity_a"], m["entity_b"], m["score_a"], m["score_b"])
    return elo


def update_standings(completed_matches, sport, league):
    """Compute standings/team stats from completed matches."""
    cfg = get_sport_config(sport)
    stats = {}

    for m in completed_matches:
        for entity, is_a, gf, ga in [
            (m["entity_a"], True, m["score_a"], m["score_b"]),
            (m["entity_b"], False, m["score_b"], m["score_a"]),
        ]:
            if entity not in stats:
                stats[entity] = {
                    "name": entity, "matches": 0, "wins": 0, "draws": 0, "losses": 0,
                    "score_for": 0, "score_against": 0,
                    "home_wins": 0, "home_draws": 0, "home_losses": 0,
                    "away_wins": 0, "away_draws": 0, "away_losses": 0,
                    "form_list": [],
                }
            s = stats[entity]
            s["matches"] += 1
            s["score_for"] += gf
            s["score_against"] += ga

            if gf > ga:
                s["wins"] += 1
                s["form_list"].append("W")
                if is_a: s["home_wins"] += 1
                else: s["away_wins"] += 1
            elif gf == ga:
                s["draws"] += 1
                s["form_list"].append("D")
                if is_a: s["home_draws"] += 1
                else: s["away_draws"] += 1
            else:
                s["losses"] += 1
                s["form_list"].append("L")
                if is_a: s["home_losses"] += 1
                else: s["away_losses"] += 1

    is_pvp = is_player_vs_player(sport, league)
    has_draws = cfg["result_labels"]["draw"] is not None

    result = []
    for entity, s in stats.items():
        points = s["wins"] * 3 + s["draws"] if has_draws else s["wins"]
        gd = s["score_for"] - s["score_against"]
        form = "".join(s["form_list"][-5:])
        result.append({
            "name": entity,
            "matches": s["matches"],
            "wins": s["wins"],
            "draws": s["draws"],
            "losses": s["losses"],
            "score_for": s["score_for"],
            "score_against": s["score_against"],
            "score_diff": gd,
            "points": points,
            "home_wins": s["home_wins"],
            "home_draws": s["home_draws"],
            "home_losses": s["home_losses"],
            "away_wins": s["away_wins"],
            "away_draws": s["away_draws"],
            "away_losses": s["away_losses"],
            "form": form,
            "win_rate": round(s["wins"] / s["matches"] * 100, 1) if s["matches"] else 0,
            "type": "player" if is_pvp else "team",
        })

    result.sort(key=lambda t: (-(t["points"] or 0), -(t["score_diff"] or 0), -(t["score_for"] or 0)))
    for i, t in enumerate(result, 1):
        t["position"] = i

    return result


def resolve_prediction_label(pred_result, labels):
    """Resolve internal prediction (a_win/b_win/Draw) to sport-specific label."""
    if pred_result == "a_win":
        return labels["home"]
    elif pred_result == "b_win":
        return labels["away"]
    else:
        return labels.get("draw") or labels["home"]


def build_historical(completed_matches, elo, sport, league, existing_historical, locked):
    """Build historical data with prediction locking."""
    cfg = get_sport_config(sport)
    labels = cfg["result_labels"]

    existing_map = {}
    for m in existing_historical:
        key = f"{m.get('date','')[:10]}|{m.get('entity_a','')}|{m.get('entity_b','')}"
        existing_map[key] = m

    new_locked = {}
    new_historical = []
    match_id = 1

    preds_dir = DATA_DIR / sport / league
    predictions_map = {}
    for p in load_json(preds_dir / "predictions.json"):
        key = f"{p.get('date','')[:10]}|{p.get('entity_a','')}|{p.get('entity_b','')}"
        predictions_map[key] = p

    for m in completed_matches:
        if m.get("score_a") is None:
            continue

        key = f"{m['date']}|{m['entity_a']}|{m['entity_b']}"
        existing = existing_map.get(key, {})
        actual = m.get("result", "Unknown")

        # Priority: locked > existing > current predictions > ELO fallback
        lock = locked.get(key)
        if lock:
            prediction = lock["prediction"]
            is_correct = lock.get("is_correct", prediction == actual) if actual != "Unknown" else None
            confidence = lock["confidence"]
            a_prob = lock["a_win_prob"]
            draw_prob = lock["draw_prob"]
            b_prob = lock["b_win_prob"]
        elif existing.get("prediction"):
            prediction = existing["prediction"]
            is_correct = existing.get("is_correct", prediction == actual) if actual != "Unknown" else None
            confidence = existing.get("confidence", 0)
            a_prob = existing.get("a_win_prob", 0)
            draw_prob = existing.get("draw_prob", 0)
            b_prob = existing.get("b_win_prob", 0)
        elif key in predictions_map:
            cp = predictions_map[key]
            prediction = cp["prediction"]
            is_correct = prediction == actual if actual != "Unknown" else None
            confidence = cp.get("confidence", 0)
            a_prob = cp.get("a_win_prob", 0)
            draw_prob = cp.get("draw_prob", 0)
            b_prob = cp.get("b_win_prob", 0)
        else:
            pred = elo.predict(m["entity_a"], m["entity_b"])
            prediction = resolve_prediction_label(pred["prediction"], labels)
            is_correct = prediction == actual if actual != "Unknown" else None
            confidence = pred["confidence"]
            a_prob = pred["a_win_prob"]
            draw_prob = pred["draw_prob"]
            b_prob = pred["b_win_prob"]

        if actual not in ("Unknown", None, ""):
            new_locked[key] = {
                "prediction": prediction,
                "confidence": confidence,
                "a_win_prob": a_prob,
                "draw_prob": draw_prob,
                "b_win_prob": b_prob,
                "is_correct": is_correct,
            }

        entry = {
            "id": match_id,
            "date": f"{m['date']} {m.get('time', '00:00')}",
            "entity_a": m["entity_a"],
            "entity_b": m["entity_b"],
            "prediction": prediction,
            "actual": actual,
            "is_correct": is_correct,
            "confidence": confidence,
            "a_win_prob": round(a_prob, 1),
            "draw_prob": round(draw_prob, 1),
            "b_win_prob": round(b_prob, 1),
            "score_a": m["score_a"],
            "score_b": m["score_b"],
            "elo_a": elo.get_rating(m["entity_a"]),
            "elo_b": elo.get_rating(m["entity_b"]),
            "elo_diff": round(elo.get_rating(m["entity_a"]) - elo.get_rating(m["entity_b"])),
            "venue": m.get("venue", "Unknown"),
            "week_number": datetime.strptime(m["date"], "%Y-%m-%d").isocalendar()[1] if m["date"] else 0,
            "year": int(m["date"][:4]) if m["date"] else 0,
            "month": int(m["date"][5:7]) if m["date"] else 0,
        }
        new_historical.append(entry)
        match_id += 1

    return new_historical, new_locked


def build_predictions(upcoming_fixtures, elo, sport, league):
    """Build predictions for upcoming fixtures using ELO."""
    cfg = get_sport_config(sport)
    labels = cfg["result_labels"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    future = [fx for fx in upcoming_fixtures if fx.get("date", "") >= today]
    if len(future) < len(upcoming_fixtures):
        print(f"  Filtered {len(upcoming_fixtures) - len(future)} past fixtures")

    predictions = []
    for i, fx in enumerate(future, 1):
        pred = elo.predict(fx["entity_a"], fx["entity_b"])
        prediction = resolve_prediction_label(pred["prediction"], labels)

        dt = datetime.strptime(fx["date"], "%Y-%m-%d")
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        predictions.append({
            "id": i,
            "date": f"{fx['date']} {fx.get('time', '00:00')}",
            "time": fx.get("time", "00:00"),
            "entity_a": fx["entity_a"],
            "entity_b": fx["entity_b"],
            "venue": fx.get("venue", "Unknown"),
            "a_win_prob": pred["a_win_prob"],
            "draw_prob": pred["draw_prob"],
            "b_win_prob": pred["b_win_prob"],
            "prediction": prediction,
            "actual_result": "Not Played",
            "score_a": None,
            "score_b": None,
            "is_correct": None,
            "confidence": pred["confidence"],
            "confidence_level": pred["confidence_level"],
            "elo_a": pred["elo_a"],
            "elo_b": pred["elo_b"],
            "elo_diff": pred["elo_diff"],
            "day_of_week": day_names[dt.weekday()],
            "week_number": dt.isocalendar()[1],
            "year": dt.year,
            "month": dt.month,
        })

    return predictions


def build_summary(predictions, historical, standings):
    """Build summary stats."""
    total = len(historical)
    correct = sum(1 for h in historical if h.get("is_correct"))

    return {
        "total_historical": total,
        "accuracy": round(correct / total * 100, 1) if total else 0,
        "correct": correct,
        "current_predictions": len(predictions),
        "high_confidence": sum(1 for p in predictions if p.get("confidence_level") == "High"),
        "medium_confidence": sum(1 for p in predictions if p.get("confidence_level") == "Medium"),
        "low_confidence": sum(1 for p in predictions if p.get("confidence_level") == "Low"),
        "entities": len(standings),
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Main Refresh Pipeline ──────────────────────────────────────────

def refresh_league(sport, league):
    """Full refresh pipeline for a single sport/league combination."""
    league_cfg = get_league_config(sport, league)
    elo_config = get_elo_config(sport)
    league_dir = DATA_DIR / sport / league

    print(f"\n--- {league_cfg['label']} ({sport}/{league}) ---")

    # 1. Fetch completed + upcoming from ESPN
    completed = fetch_completed_matches(sport, league, days_back=14)
    upcoming = fetch_upcoming_fixtures(sport, league, days_ahead=14)
    print(f"  {len(completed)} completed, {len(upcoming)} upcoming from ESPN")

    if not completed and not upcoming:
        print(f"  No data available, skipping")
        return

    # 2. Build ELO from history
    elo = build_elo_from_history(completed, elo_config)
    print(f"  ELO built from {len(completed)} matches ({len(elo.ratings)} entities rated)")

    # 3. Load existing data
    existing_historical = load_json(league_dir / "historical.json")
    locked_path = league_dir / "locked_predictions.json"
    locked = load_json(locked_path) if locked_path.exists() else {}

    # 4. Build standings
    standings = update_standings(completed, sport, league)
    for s in standings:
        s["rating"] = elo.get_rating(s["name"])

    # 5. Build historical with prediction locking
    historical, new_locked = build_historical(
        completed, elo, sport, league, existing_historical, locked
    )

    # 6. Build predictions
    predictions = build_predictions(upcoming, elo, sport, league)

    # 7. Build summary
    summary = build_summary(predictions, historical, standings)

    # 8. Save all files
    save_json(league_dir / "predictions.json", predictions)
    save_json(league_dir / "historical.json", historical)
    save_json(league_dir / "standings.json", standings)
    save_json(league_dir / "summary.json", summary)
    save_json(locked_path, new_locked)

    print(f"  Done: {len(predictions)} predictions, {len(historical)} historical, {len(standings)} entities")


def refresh_all():
    """Refresh all configured sport/league combinations."""
    print("=== Multi-Sport Data Refresh (ESPN API) ===\n")
    for sport, league, label in list_all_leagues():
        try:
            refresh_league(sport, league)
        except Exception as e:
            print(f"  ERROR refreshing {sport}/{league}: {e}")
    print("\n=== Refresh Complete ===")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Refresh specific sport/league: python refresh_data.py soccer eng.1
        refresh_league(sys.argv[1], sys.argv[2])
    else:
        refresh_all()
