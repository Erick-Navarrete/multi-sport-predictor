"""Sport and league configuration registry.

Defines all supported sports/leagues with ESPN API slugs,
competition type, ELO parameters, and display metadata.
"""

# Competition types
TEAM_VS_TEAM = "team_vs_team"      # soccer, nba, nfl, mlb, nhl
PLAYER_VS_PLAYER = "player_vs_player"  # tennis, mma/ufc

SPORTS = {
    "soccer": {
        "label": "Soccer",
        "icon": " futbol-ball",
        "leagues": {
            "eng.1": {"label": "Premier League", "country": "England", "type": TEAM_VS_TEAM},
            "esp.1": {"label": "La Liga", "country": "Spain", "type": TEAM_VS_TEAM},
            "ger.1": {"label": "Bundesliga", "country": "Germany", "type": TEAM_VS_TEAM},
            "ita.1": {"label": "Serie A", "country": "Italy", "type": TEAM_VS_TEAM},
            "fra.1": {"label": "Ligue 1", "country": "France", "type": TEAM_VS_TEAM},
        },
        "elo": {"k": 32, "home_advantage": 65, "draw_margin": 8},
        "result_labels": {"home": "Home Win", "away": "Away Win", "draw": "Draw"},
        "score_field": "goals",
    },
    "basketball": {
        "label": "Basketball",
        "icon": " basketball",
        "leagues": {
            "nba": {"label": "NBA", "country": "USA", "type": TEAM_VS_TEAM},
        },
        "elo": {"k": 20, "home_advantage": 68, "draw_margin": 0},
        "result_labels": {"home": "Home Win", "away": "Away Win", "draw": None},
        "score_field": "points",
    },
    "football": {
        "label": "Football",
        "icon": " football",
        "leagues": {
            "nfl": {"label": "NFL", "country": "USA", "type": TEAM_VS_TEAM},
        },
        "elo": {"k": 20, "home_advantage": 55, "draw_margin": 0},
        "result_labels": {"home": "Home Win", "away": "Away Win", "draw": None},
        "score_field": "points",
    },
    "baseball": {
        "label": "Baseball",
        "icon": " baseball",
        "leagues": {
            "mlb": {"label": "MLB", "country": "USA", "type": TEAM_VS_TEAM},
        },
        "elo": {"k": 16, "home_advantage": 24, "draw_margin": 0},
        "result_labels": {"home": "Home Win", "away": "Away Win", "draw": None},
        "score_field": "runs",
    },
    "hockey": {
        "label": "Hockey",
        "icon": " hockey-puck",
        "leagues": {
            "nhl": {"label": "NHL", "country": "USA", "type": TEAM_VS_TEAM},
        },
        "elo": {"k": 20, "home_advantage": 55, "draw_margin": 0},
        "result_labels": {"home": "Home Win", "away": "Away Win", "draw": None},
        "score_field": "goals",
    },
    "tennis": {
        "label": "Tennis",
        "icon": " table-tennis-paddle-ball",
        "leagues": {
            "atp": {"label": "ATP Tour", "country": "World", "type": PLAYER_VS_PLAYER},
            "wta": {"label": "WTA Tour", "country": "World", "type": PLAYER_VS_PLAYER},
        },
        "elo": {"k": 24, "home_advantage": 0, "draw_margin": 0},
        "result_labels": {"home": "P1 Win", "away": "P2 Win", "draw": None},
        "score_field": "sets",
    },
    "mma": {
        "label": "MMA",
        "icon": " hand-fist",
        "leagues": {
            "ufc": {"label": "UFC", "country": "World", "type": PLAYER_VS_PLAYER},
        },
        "elo": {"k": 28, "home_advantage": 0, "draw_margin": 0},
        "result_labels": {"home": "Fighter 1 Win", "away": "Fighter 2 Win", "draw": "Draw"},
        "score_field": "decision",
    },
}

# Default sport/league on first load
DEFAULT_SPORT = "soccer"
DEFAULT_LEAGUE = "eng.1"


def get_sport_config(sport):
    """Get config for a sport, raises KeyError if invalid."""
    return SPORTS[sport]


def get_league_config(sport, league):
    """Get config for a league within a sport."""
    return SPORTS[sport]["leagues"][league]


def get_elo_config(sport):
    """Get ELO parameters for a sport."""
    return SPORTS[sport]["elo"]


def list_all_leagues():
    """Return flat list of (sport, league_slug, label) for all leagues."""
    result = []
    for sport_key, sport in SPORTS.items():
        for league_key, league in sport["leagues"].items():
            result.append((sport_key, league_key, league["label"]))
    return result


def is_player_vs_player(sport, league):
    """Check if a league uses player-vs-player (not team-vs-team)."""
    return SPORTS[sport]["leagues"][league]["type"] == PLAYER_VS_PLAYER
