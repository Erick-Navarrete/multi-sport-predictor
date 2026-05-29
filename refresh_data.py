"""Multi-sport prediction data refresh — ESPN API + The-Odds-API.

Fetches completed results and upcoming fixtures from ESPN scoreboard,
fetches betting odds from The-Odds-API (when key is configured),
generates blended predictions (ELO + Colley + Form + Odds),
and updates JSON data files per sport/league in data/{sport}/{league}/.
"""

import json
import os
import requests
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sports_config import (
    SPORTS, get_elo_config, get_sport_config, get_league_config,
    list_all_leagues, is_player_vs_player,
    ODDS_API_KEY, ODDS_API_BASE, get_odds_sport_key,
    get_blend_weights, get_season_start,
)

ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"


def load_json(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size = len(data) if isinstance(data, list) else "dict"
    print(f"  Saved {path.relative_to(DATA_DIR)} ({size})")


# ── ESPN API Fetchers ──────────────────────────────────────────────

ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"


def fetch_espn_scoreboard(sport, league, date_str):
    url = f"{ESPN_SITE_BASE}/{sport}/{league}/scoreboard?dates={date_str}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ESPN fetch failed for {sport}/{league} on {date_str}: {e}")
    return None


def _extract_competitions(event):
    comps = []
    if event.get("competitions"):
        for comp in event["competitions"]:
            comps.append(comp)
    for g in event.get("groupings", []):
        for comp in g.get("competitions", []):
            comps.append(comp)
    return comps


def _get_entity_name(competitor):
    if "team" in competitor:
        return competitor["team"].get("shortDisplayName") or competitor["team"].get("displayName", "")
    if "athlete" in competitor:
        return competitor["athlete"].get("displayName", "")
    return ""


def parse_espn_competition(comp, sport, league, event_date, event_time):
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None

    entity_a = entity_b = None
    score_a = score_b = 0
    winner = None
    sorted_comps = sorted(competitors, key=lambda c: c.get("order", 99))

    for idx, c in enumerate(sorted_comps):
        name = _get_entity_name(c)
        score = int(c.get("score", 0))
        is_winner = c.get("winner", False)

        if c.get("homeAway") == "home" or (idx == 0 and not c.get("homeAway")):
            entity_a = name
            score_a = score
            if is_winner: winner = "a"
        else:
            entity_b = name
            score_b = score
            if is_winner: winner = "b"

    if not entity_a or not entity_b:
        return None

    comp_status = comp.get("status", {}).get("type", {})
    state = comp_status.get("state", "pre")

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

    # Extract ESPN odds if present (DraftKings etc. embedded in scoreboard)
    espn_odds = None
    for odds_entry in comp.get("odds", []):
        if not odds_entry or not isinstance(odds_entry, dict):
            continue
        provider = odds_entry.get("provider") or {}
        # Accept DraftKings (id 100 or 58)
        if str(provider.get("id", "")) not in ("100", "58"):
            continue
        # Try nested moneyline format (newer ESPN API: odds.moneyline.home.close.odds)
        ml = odds_entry.get("moneyline") or {}
        home_ml = None
        away_ml = None
        if ml:
            home_ml = ((ml.get("home") or {}).get("close") or {}).get("odds")
            away_ml = ((ml.get("away") or {}).get("close") or {}).get("odds")
        # Fallback to flat format (older API: homeTeamOdds.value)
        if not home_ml:
            home_ml = (odds_entry.get("homeTeamOdds") or {}).get("value")
        if not away_ml:
            away_ml = (odds_entry.get("awayTeamOdds") or {}).get("value")
        if home_ml and away_ml:
            espn_odds = {
                "home_ml": home_ml,
                "away_ml": away_ml,
                "spread": odds_entry.get("details"),
                "over_under": odds_entry.get("overUnder"),
            }
            break

    match = {
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
    if espn_odds:
        match["espn_odds"] = espn_odds
    return match


def _fetch_day_matches(sport, league, date_str):
    """Fetch and parse matches for a single date. Returns (completed, upcoming, has_data)."""
    data = fetch_espn_scoreboard(sport, league, date_str)
    if not data or not data.get("events"):
        return [], [], False
    completed = []
    upcoming = []
    has_data = False
    for ev in data["events"]:
        event_date = (ev.get("date", "")[:10])
        event_time = (ev.get("date", "")[11:16]) or "00:00"
        for comp in _extract_competitions(ev):
            comp_status = comp.get("status", {}).get("type", {})
            state = comp_status.get("state")
            if state not in ("post", "pre"):
                continue
            has_data = True
            match = parse_espn_competition(comp, sport, league, event_date, event_time)
            if not match or not match["entity_a"] or not match["entity_b"]:
                continue
            if state == "post":
                completed.append(match)
            elif state == "pre":
                upcoming.append(match)
    return completed, upcoming, has_data


def fetch_league_data(sport, league, days_back=60, days_ahead=14):
    """Fetch completed + upcoming matches in a single concurrent pass.

    Combines past and future date fetches into one ThreadPoolExecutor batch
    for maximum parallelism and minimal latency.
    """
    today = datetime.now(timezone.utc).date()
    completed = []
    upcoming = []
    seen_completed = set()
    seen_upcoming = set()

    # Generate all date strings: past + future
    past_dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back)]
    future_dates = [(today + timedelta(days=i + 1)).strftime("%Y%m%d") for i in range(days_ahead)]
    all_dates = past_dates + future_dates

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_day_matches, sport, league, ds): ds for ds in all_dates}
        for future in as_completed(futures):
            day_completed, day_upcoming, _ = future.result()
            for match in day_completed:
                key = f"{match['date']}|{match['entity_a']}|{match['entity_b']}"
                if key not in seen_completed:
                    seen_completed.add(key)
                    completed.append(match)
            for match in day_upcoming:
                key = f"{match['date']}|{match['entity_a']}|{match['entity_b']}"
                if key not in seen_upcoming:
                    seen_upcoming.add(key)
                    upcoming.append(match)

    completed.sort(key=lambda m: m["date"])
    upcoming.sort(key=lambda m: m["date"])
    return completed, upcoming


# ── Season Status Detection ────────────────────────────────────────

def get_days_back(sport):
    """Compute days_back from season start to today, capped at 120 days.

    120 days provides 4 months of recent history which is sufficient for
    ELO/Colley/Form signals. Older accuracy is preserved via locked_predictions.
    """
    try:
        season_start = get_season_start(sport)
        today = datetime.now(timezone.utc).date()
        delta = (today - season_start).days
        return min(max(delta, 14), 120)
    except Exception:
        return 60


def detect_season_status(sport, completed, upcoming):
    if upcoming:
        return "in_season"
    if completed:
        return "off_season"
    return "dormant"


# ── ELO Rating System (with decay) ─────────────────────────────────

class SportELO:
    def __init__(self, k=32, home_advantage=65, draw_margin=8, decay=0.98):
        self.k = k
        self.home_advantage = home_advantage
        self.draw_margin = draw_margin
        self.decay = decay
        self.ratings = {}
        self.match_count = {}

    def get_rating(self, entity):
        return self.ratings.get(entity, 1500)

    def expected_score(self, ra, rb):
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def predict(self, entity_a, entity_b):
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
        # Apply decay to all existing ratings before each update
        if self.decay < 1.0:
            for e in self.ratings:
                self.ratings[e] = 1500 + (self.ratings[e] - 1500) * self.decay

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

        self.ratings[entity_a] = round(self.get_rating(entity_a) + self.k * (s_a - e_a))
        self.ratings[entity_b] = round(self.get_rating(entity_b) + self.k * (s_b - e_b))

        self.match_count[entity_a] = self.match_count.get(entity_a, 0) + 1
        self.match_count[entity_b] = self.match_count.get(entity_b, 0) + 1


def build_elo_from_history(completed_matches, elo_config):
    elo = SportELO(**elo_config)
    for m in completed_matches:
        if m.get("score_a") is not None and m.get("score_b") is not None:
            elo.update(m["entity_a"], m["entity_b"], m["score_a"], m["score_b"])
    return elo


# ── Colley Matrix Rating System ────────────────────────────────────

def build_colley_ratings(completed_matches):
    """Colley Matrix: strength-of-schedule-adjusted ratings via linear algebra.

    Solves C * r = b where:
    C[i][i] = 2 + n_i (games played by team i)
    C[i][j] = -n_ij (times team i played team j)
    b[i] = 1 + (w_i - l_i) / 2
    No external deps — uses simple Gaussian elimination.
    """

    if not completed_matches:
        return {}

    entities = set()
    for m in completed_matches:
        entities.add(m["entity_a"])
        entities.add(m["entity_b"])
    entities = sorted(entities)
    n = len(entities)
    if n == 0:
        return {}
    idx = {e: i for i, e in enumerate(entities)}

    # Build C matrix and b vector
    C = [[0.0] * n for _ in range(n)]
    b = [0.0] * n

    wins = [0] * n
    losses = [0] * n

    for m in completed_matches:
        a_idx = idx[m["entity_a"]]
        b_idx = idx[m["entity_b"]]
        C[a_idx][b_idx] -= 1
        C[b_idx][a_idx] -= 1
        C[a_idx][a_idx] += 1
        C[b_idx][b_idx] += 1

        if m["score_a"] > m["score_b"]:
            wins[a_idx] += 1
            losses[b_idx] += 1
        elif m["score_a"] < m["score_b"]:
            wins[b_idx] += 1
            losses[a_idx] += 1

    for i in range(n):
        C[i][i] += 2
        b[i] = 1.0 + (wins[i] - losses[i]) / 2.0

    # Gaussian elimination (no numpy needed)
    for col in range(n):
        max_row = col
        for row in range(col + 1, n):
            if abs(C[row][col]) > abs(C[max_row][col]):
                max_row = row
        C[col], C[max_row] = C[max_row], C[col]
        b[col], b[max_row] = b[max_row], b[col]

        if abs(C[col][col]) < 1e-12:
            continue

        for row in range(col + 1, n):
            factor = C[row][col] / C[col][col]
            for j in range(col, n):
                C[row][j] -= factor * C[col][j]
            b[row] -= factor * b[col]

    # Back substitution
    r = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(C[i][i]) < 1e-12:
            r[i] = 0.5
            continue
        s = b[i]
        for j in range(i + 1, n):
            s -= C[i][j] * r[j]
        r[i] = s / C[i][i]

    # Normalize to ELO-like scale (1500 center, ~300 spread)
    min_r, max_r = min(r), max(r)
    rng = max_r - min_r if max_r > min_r else 1.0
    ratings = {}
    for i, e in enumerate(entities):
        scaled = 1500 + (r[i] - min_r) / rng * 600 - 300
        ratings[e] = round(scaled)
    return ratings


def colley_predict(colley_ratings, entity_a, entity_b, home_advantage=65, draw_margin=8):
    r_a = colley_ratings.get(entity_a, 1500) + home_advantage
    r_b = colley_ratings.get(entity_b, 1500)
    e_a = 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))

    draw_prob = 0.0
    if draw_margin > 0:
        draw_prob = max(12.0, 26.0 - abs(r_a - r_b) * 0.06)
        draw_prob = round(draw_prob, 1)

    remaining = 100.0 - draw_prob
    a_win_prob = round(remaining * e_a, 1)
    b_win_prob = round(100.0 - a_win_prob - draw_prob, 1)

    top = max(a_win_prob, draw_prob, b_win_prob)
    if draw_margin > 0 and top - draw_prob < draw_margin:
        prediction = "Draw"
    elif a_win_prob >= b_win_prob:
        prediction = "a_win"
    else:
        prediction = "b_win"

    return {"a_win_prob": a_win_prob, "draw_prob": draw_prob, "b_win_prob": b_win_prob, "prediction": prediction}


# ── Form Signal ────────────────────────────────────────────────────

def compute_form_signal(entity, completed_matches, window=5):
    """Compute a 0-100 form score from last N matches, most recent first."""
    relevant = [m for m in completed_matches if m["entity_a"] == entity or m["entity_b"] == entity]
    relevant = relevant[-window:]
    if not relevant:
        return 50.0

    score = 0.0
    total_weight = 0.0
    for i, m in enumerate(relevant):
        weight = (i + 1) / len(relevant)
        if m["entity_a"] == entity:
            gf, ga = m["score_a"], m["score_b"]
        else:
            gf, ga = m["score_b"], m["score_a"]
        if gf > ga:
            score += 100 * weight
        elif gf == ga:
            score += 50 * weight
        else:
            score += 0 * weight
        total_weight += weight

    return round(score / total_weight, 1) if total_weight else 50.0


def form_predict(form_signal_a, form_signal_b, draw_margin=8):
    diff = form_signal_a - form_signal_b
    e_a = 1.0 / (1.0 + 10 ** (-diff / 40.0))

    draw_prob = 0.0
    if draw_margin > 0:
        draw_prob = max(12.0, 26.0 - abs(diff) * 0.3)
        draw_prob = round(draw_prob, 1)

    remaining = 100.0 - draw_prob
    a_win_prob = round(remaining * e_a, 1)
    b_win_prob = round(100.0 - a_win_prob - draw_prob, 1)

    top = max(a_win_prob, draw_prob, b_win_prob)
    if draw_margin > 0 and top - draw_prob < draw_margin:
        prediction = "Draw"
    elif a_win_prob >= b_win_prob:
        prediction = "a_win"
    else:
        prediction = "b_win"

    return {"a_win_prob": a_win_prob, "draw_prob": draw_prob, "b_win_prob": b_win_prob, "prediction": prediction}


# ── The-Odds-API Integration ───────────────────────────────────────

def fetch_odds_for_league(sport, league):
    """Fetch current odds from The-Odds-API for a sport/league."""
    if not ODDS_API_KEY:
        return {}

    sport_key = get_odds_sport_key(sport, league)
    if not sport_key:
        return {}

    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            remaining = r.headers.get("x-requests-remaining", "?")
            print(f"  Odds API: {len(r.json())} events fetched ({remaining} credits remaining)")
            odds_map = {}
            for event in r.json():
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                key = f"{home}|{away}"
                best_h2h = None
                for bm in event.get("bookmakers", []):
                    for market in bm.get("markets", []):
                        if market["key"] == "h2h":
                            outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                            best_h2h = outcomes
                            break
                    if best_h2h:
                        break
                if best_h2h:
                    odds_map[key] = best_h2h
            return odds_map
        else:
            print(f"  Odds API error: {r.status_code}")
    except Exception as e:
        print(f"  Odds API fetch failed: {e}")
    return {}


def odds_implied_prob(odds_data, entity_a, entity_b):
    """Convert decimal odds to implied probabilities, removing vig.

    Handles both 2-way (NBA/MLB etc) and 3-way (soccer with draw) markets.
    Uses fuzzy name matching since Odds API names may differ from ESPN short names.
    """
    best_match = None
    best_score = 0

    for key, outcomes in odds_data.items():
        home_name = key.split("|")[0]
        away_name = key.split("|")[1]
        # Fuzzy match: check if either name contains or is contained in the other
        a_match = (entity_a.lower() in home_name.lower() or home_name.lower() in entity_a.lower())
        b_match = (entity_b.lower() in away_name.lower() or away_name.lower() in entity_b.lower())
        # Also try swapped (Odds API home/away might differ from ESPN)
        a_swap = (entity_a.lower() in away_name.lower() or away_name.lower() in entity_a.lower())
        b_swap = (entity_b.lower() in home_name.lower() or home_name.lower() in entity_b.lower())
        score = 0
        if a_match and b_match: score = 2
        elif a_swap and b_swap: score = 1
        if score > best_score:
            best_score = score
            best_match = (home_name, away_name, outcomes)

    if not best_match:
        return None

    home_name, away_name, outcomes = best_match
    home_odds = outcomes.get(home_name) or outcomes.get(entity_a)
    away_odds = outcomes.get(away_name) or outcomes.get(entity_b)
    draw_odds = outcomes.get("Draw")

    if not home_odds or not away_odds:
        return None
    if home_odds <= 0 or away_odds <= 0:
        return None

    imp_a = 1.0 / home_odds
    imp_b = 1.0 / away_odds
    imp_d = (1.0 / draw_odds) if draw_odds and draw_odds > 0 else 0.0

    total = imp_a + imp_b + imp_d
    if total <= 0:
        return None
    vig_free_a = imp_a / total
    vig_free_b = imp_b / total
    vig_free_d = imp_d / total

    return {
        "a_win_prob": round(vig_free_a * 100, 1),
        "b_win_prob": round(vig_free_b * 100, 1),
        "draw_prob": round(vig_free_d * 100, 1),
        "prediction": "a_win" if vig_free_a > vig_free_b else "b_win",
    }


# ── ESPN Embedded Odds Fallback ────────────────────────────────────

def espn_odds_to_prob(espn_odds, has_draw=False):
    """Convert ESPN scoreboard embedded odds (DraftKings) to implied probabilities.

    ESPN moneyline values are American odds (e.g. -150, +200).
    American odds: negative = amount to bet to win $100, positive = payout on $100 bet.
    """
    if not espn_odds:
        return None

    home_ml = espn_odds.get("home_ml")
    away_ml = espn_odds.get("away_ml")
    if home_ml is None or away_ml is None:
        return None

    try:
        home_ml = int(str(home_ml).replace("+", ""))
        away_ml = int(str(away_ml).replace("+", ""))
    except (ValueError, TypeError):
        return None

    if home_ml == 0 or away_ml == 0:
        return None

    # Convert American odds to implied probability
    def am_prob(odds):
        if odds > 0:
            return 100.0 / (odds + 100.0)
        else:
            return abs(odds) / (abs(odds) + 100.0)

    imp_a = am_prob(home_ml)
    imp_b = am_prob(away_ml)
    imp_d = 0.0

    # If spread is "PK" or empty and no over_under, assume 2-way market
    # For soccer, 3-way markets may have over_under but spread indicates the market
    total = imp_a + imp_b + imp_d
    if total <= 0:
        return None

    vig_free_a = imp_a / total
    vig_free_b = imp_b / total
    vig_free_d = imp_d / total

    return {
        "a_win_prob": round(vig_free_a * 100, 1),
        "b_win_prob": round(vig_free_b * 100, 1),
        "draw_prob": round(vig_free_d * 100, 1),
        "prediction": "a_win" if vig_free_a > vig_free_b else "b_win",
        "source": "espn_embedded",
    }


# ── Blend Predictions ──────────────────────────────────────────────

def blend_predictions(elo_pred, colley_pred, form_pred, odds_pred, weights):
    """Blend ELO, Colley, Form, and Odds predictions using configured weights."""
    has_odds = odds_pred is not None

    if has_odds:
        w_elo = weights["elo"]
        w_colley = weights["colley"]
        w_form = weights["form"]
        w_odds = weights["odds"]
    else:
        # Redistribute odds weight proportionally to ELO/Colley/Form
        total_no_odds = weights["elo"] + weights["colley"] + weights["form"]
        w_elo = weights["elo"] / total_no_odds
        w_colley = weights["colley"] / total_no_odds
        w_form = weights["form"] / total_no_odds
        w_odds = 0.0

    a_prob = w_elo * elo_pred["a_win_prob"]
    a_prob += w_colley * colley_pred["a_win_prob"]
    a_prob += w_form * form_pred["a_win_prob"]
    if has_odds:
        a_prob += w_odds * odds_pred["a_win_prob"]

    draw_prob = w_elo * elo_pred.get("draw_prob", 0)
    draw_prob += w_colley * colley_pred.get("draw_prob", 0)
    draw_prob += w_form * form_pred.get("draw_prob", 0)
    if has_odds:
        draw_prob += w_odds * odds_pred.get("draw_prob", 0)

    a_prob = round(a_prob, 1)
    draw_prob = round(draw_prob, 1)
    b_prob = round(100.0 - a_prob - draw_prob, 1)

    top = max(a_prob, draw_prob, b_prob)
    has_draw = elo_pred.get("draw_prob", 0) > 0
    if has_draw and top - draw_prob < 8:
        prediction = "Draw"
    elif a_prob >= b_prob:
        prediction = "a_win"
    else:
        prediction = "b_win"

    confidence = round(top, 1)
    confidence_level = "High" if confidence >= 70 else "Medium" if confidence >= 50 else "Low"

    return {
        "a_win_prob": a_prob,
        "draw_prob": draw_prob,
        "b_win_prob": b_prob,
        "prediction": prediction,
        "confidence": confidence,
        "confidence_level": confidence_level,
        "sources": {
            "elo": {"a": elo_pred["a_win_prob"], "d": elo_pred.get("draw_prob", 0), "b": elo_pred["b_win_prob"]},
            "colley": {"a": colley_pred["a_win_prob"], "d": colley_pred.get("draw_prob", 0), "b": colley_pred["b_win_prob"]},
            "form": {"a": form_pred["a_win_prob"], "d": form_pred.get("draw_prob", 0), "b": form_pred["b_win_prob"]},
            "odds": {"a": odds_pred["a_win_prob"], "d": odds_pred.get("draw_prob", 0), "b": odds_pred["b_win_prob"]} if has_odds else None,
        },
        "weights_used": {"elo": w_elo, "colley": w_colley, "form": w_form, "odds": w_odds},
    }


# ── Data Pipeline Functions ────────────────────────────────────────

def update_standings(completed_matches, sport, league):
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
            "wins": s["wins"], "draws": s["draws"], "losses": s["losses"],
            "score_for": s["score_for"], "score_against": s["score_against"],
            "score_diff": gd, "points": points,
            "home_wins": s["home_wins"], "home_draws": s["home_draws"], "home_losses": s["home_losses"],
            "away_wins": s["away_wins"], "away_draws": s["away_draws"], "away_losses": s["away_losses"],
            "form": form,
            "win_rate": round(s["wins"] / s["matches"] * 100, 1) if s["matches"] else 0,
            "type": "player" if is_pvp else "team",
        })

    result.sort(key=lambda t: (-(t["points"] or 0), -(t["score_diff"] or 0), -(t["score_for"] or 0)))
    for i, t in enumerate(result, 1):
        t["position"] = i
    return result


def resolve_prediction_label(pred_result, labels):
    if pred_result == "a_win":
        return labels["home"]
    elif pred_result == "b_win":
        return labels["away"]
    else:
        return labels.get("draw") or labels["home"]


def build_historical(completed_matches, elo, colley_ratings, sport, league, existing_historical, locked, weights):
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
            elo_pred = elo.predict(m["entity_a"], m["entity_b"])
            colley_pred = colley_predict(colley_ratings, m["entity_a"], m["entity_b"], elo.home_advantage, elo.draw_margin)
            form_a = compute_form_signal(m["entity_a"], completed_matches)
            form_b = compute_form_signal(m["entity_b"], completed_matches)
            form_pred = form_predict(form_a, form_b, elo.draw_margin)
            blended = blend_predictions(elo_pred, colley_pred, form_pred, None, weights)
            prediction = resolve_prediction_label(blended["prediction"], labels)
            is_correct = prediction == actual if actual != "Unknown" else None
            confidence = blended["confidence"]
            a_prob = blended["a_win_prob"]
            draw_prob = blended["draw_prob"]
            b_prob = blended["b_win_prob"]

        if actual not in ("Unknown", None, ""):
            new_locked[key] = {
                "prediction": prediction, "confidence": confidence,
                "a_win_prob": a_prob, "draw_prob": draw_prob, "b_win_prob": b_prob,
                "is_correct": is_correct,
            }

        entry = {
            "id": match_id,
            "date": f"{m['date']} {m.get('time', '00:00')}",
            "entity_a": m["entity_a"], "entity_b": m["entity_b"],
            "prediction": prediction, "actual": actual, "is_correct": is_correct,
            "confidence": confidence,
            "a_win_prob": round(a_prob, 1), "draw_prob": round(draw_prob, 1), "b_win_prob": round(b_prob, 1),
            "score_a": m["score_a"], "score_b": m["score_b"],
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


def build_predictions(upcoming_fixtures, elo, colley_ratings, completed_matches, sport, league, odds_data, weights):
    cfg = get_sport_config(sport)
    labels = cfg["result_labels"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    future = [fx for fx in upcoming_fixtures if fx.get("date", "") >= today]
    if len(future) < len(upcoming_fixtures):
        print(f"  Filtered {len(upcoming_fixtures) - len(future)} past fixtures")

    predictions = []
    for i, fx in enumerate(future, 1):
        elo_pred = elo.predict(fx["entity_a"], fx["entity_b"])
        colley_pred = colley_predict(colley_ratings, fx["entity_a"], fx["entity_b"], elo.home_advantage, elo.draw_margin)
        form_a = compute_form_signal(fx["entity_a"], completed_matches)
        form_b = compute_form_signal(fx["entity_b"], completed_matches)
        form_pred = form_predict(form_a, form_b, elo.draw_margin)
        odds_pred = odds_implied_prob(odds_data, fx["entity_a"], fx["entity_b"])
        odds_source = "odds_api" if odds_pred else None
        # Fallback to ESPN embedded odds (DraftKings) when Odds API has no match
        if not odds_pred and fx.get("espn_odds"):
            odds_pred = espn_odds_to_prob(fx["espn_odds"], has_draw=(elo.draw_margin > 0))
            odds_source = "espn_embedded" if odds_pred else None
        blended = blend_predictions(elo_pred, colley_pred, form_pred, odds_pred, weights)
        prediction = resolve_prediction_label(blended["prediction"], labels)

        dt = datetime.strptime(fx["date"], "%Y-%m-%d")
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        entry = {
            "id": i,
            "date": f"{fx['date']} {fx.get('time', '00:00')}",
            "time": fx.get("time", "00:00"),
            "entity_a": fx["entity_a"], "entity_b": fx["entity_b"],
            "venue": fx.get("venue", "Unknown"),
            "a_win_prob": blended["a_win_prob"],
            "draw_prob": blended["draw_prob"],
            "b_win_prob": blended["b_win_prob"],
            "prediction": prediction,
            "actual_result": "Not Played",
            "score_a": None, "score_b": None,
            "is_correct": None,
            "confidence": blended["confidence"],
            "confidence_level": blended["confidence_level"],
            "elo_a": elo_pred["elo_a"], "elo_b": elo_pred["elo_b"], "elo_diff": elo_pred["elo_diff"],
            "colley_a": colley_ratings.get(fx["entity_a"], 1500),
            "colley_b": colley_ratings.get(fx["entity_b"], 1500),
            "form_a": form_a, "form_b": form_b,
            "odds_available": odds_pred is not None,
            "odds_source": odds_source,
            "sources": blended.get("sources", {}),
            "weights_used": blended.get("weights_used", {}),
            "day_of_week": day_names[dt.weekday()],
            "week_number": dt.isocalendar()[1],
            "year": dt.year, "month": dt.month,
        }
        # Include ESPN odds if embedded in scoreboard
        if fx.get("espn_odds"):
            entry["espn_odds"] = fx["espn_odds"]
        predictions.append(entry)

    return predictions


def build_summary(predictions, historical, standings, season_status):
    total = len(historical)
    correct = sum(1 for h in historical if h.get("is_correct"))
    odds_count = sum(1 for p in predictions if p.get("odds_available"))
    espn_odds_count = sum(1 for p in predictions if p.get("odds_source") == "espn_embedded")

    return {
        "total_historical": total,
        "accuracy": round(correct / total * 100, 1) if total else 0,
        "correct": correct,
        "current_predictions": len(predictions),
        "high_confidence": sum(1 for p in predictions if p.get("confidence_level") == "High"),
        "medium_confidence": sum(1 for p in predictions if p.get("confidence_level") == "Medium"),
        "low_confidence": sum(1 for p in predictions if p.get("confidence_level") == "Low"),
        "entities": len(standings),
        "odds_coverage": odds_count,
        "espn_odds_coverage": espn_odds_count,
        "season_status": season_status,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Main Refresh Pipeline ──────────────────────────────────────────

def refresh_league(sport, league):
    league_cfg = get_league_config(sport, league)
    elo_config = get_elo_config(sport)
    weights = get_blend_weights(sport)
    league_dir = DATA_DIR / sport / league

    print(f"\n--- {league_cfg['label']} ({sport}/{league}) ---")

    # 1. Fetch completed + upcoming from ESPN (single concurrent pass)
    days_back = get_days_back(sport)
    completed, upcoming = fetch_league_data(sport, league, days_back=days_back, days_ahead=14)
    print(f"  {days_back} days back + 14 days ahead (concurrent)")
    print(f"  {len(completed)} completed, {len(upcoming)} upcoming from ESPN")

    # 2. Detect season status
    season_status = detect_season_status(sport, completed, upcoming)
    print(f"  Season status: {season_status}")

    if not completed and not upcoming:
        # Off-season: write minimal summary so frontend shows status
        summary = {"total_historical": 0, "accuracy": 0, "correct": 0,
                    "current_predictions": 0, "entities": 0,
                    "season_status": season_status,
                    "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
        save_json(league_dir / "summary.json", summary)
        # Preserve existing data if present, write empties otherwise
        if not (league_dir / "predictions.json").exists():
            save_json(league_dir / "predictions.json", [])
        if not (league_dir / "standings.json").exists():
            save_json(league_dir / "standings.json", [])
        print(f"  No data available (off-season), summary saved")
        return

    # 3. Build ELO from history (with decay)
    elo = build_elo_from_history(completed, elo_config)
    print(f"  ELO built from {len(completed)} matches ({len(elo.ratings)} entities rated)")

    # 4. Build Colley ratings
    colley_ratings = build_colley_ratings(completed)
    print(f"  Colley ratings built ({len(colley_ratings)} entities)")

    # 5. Fetch odds from The-Odds-API
    odds_data = fetch_odds_for_league(sport, league)

    # 6. Load existing data
    existing_historical = load_json(league_dir / "historical.json")
    locked_path = league_dir / "locked_predictions.json"
    locked = load_json(locked_path) if locked_path.exists() else {}

    # 7. Build standings
    standings = update_standings(completed, sport, league)
    for s in standings:
        s["rating"] = elo.get_rating(s["name"])
        s["colley_rating"] = colley_ratings.get(s["name"], 1500)

    # 8. Build historical with prediction locking
    historical, new_locked = build_historical(
        completed, elo, colley_ratings, sport, league, existing_historical, locked, weights
    )

    # 9. Build predictions (blended)
    predictions = build_predictions(upcoming, elo, colley_ratings, completed, sport, league, odds_data, weights)

    # 10. Build summary
    summary = build_summary(predictions, historical, standings, season_status)

    # 11. Save all files
    save_json(league_dir / "predictions.json", predictions)
    save_json(league_dir / "historical.json", historical)
    save_json(league_dir / "standings.json", standings)
    save_json(league_dir / "summary.json", summary)
    save_json(locked_path, new_locked)

    print(f"  Done: {len(predictions)} predictions, {len(historical)} historical, {len(standings)} entities")


def refresh_all():
    print("=== Multi-Sport Data Refresh (ESPN + Odds API) ===\n")
    for sport, league, label in list_all_leagues():
        try:
            refresh_league(sport, league)
        except Exception as e:
            print(f"  ERROR refreshing {sport}/{league}: {e}")
    print("\n=== Refresh Complete ===")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        refresh_league(sys.argv[1], sys.argv[2])
    else:
        refresh_all()
