KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA  = "https://gamma-api.polymarket.com"

SPORT_KEYS = {
    "mlb":   "mlb",
    "nfl":   "nfl",
    "nba":   "nba",
    "nhl":   "nhl",
    "ncaaf": "ncaaf",
    "ncaab": "ncaab",
}

KALSHI_SERIES = {
    "mlb":   ["KXMLBGAME"],
    "nfl":   ["KXNFLGAME"],
    "nba":   ["KXNBAGAME"],
    "nhl":   ["KXNHLGAME"],
    "ncaaf": ["KXNCAAFGAME"],
    "ncaab": ["KXNCAABGAME"],
}

POLY_SERIES = {
    "mlb":   3,
    "nfl":   10187,
    "nba":   10345,
    "nhl":   10346,
    "ncaaf": 10210,
    "ncaab": 39,
}

FETCH_INTERVAL_SECONDS = 120
LOG_DIR    = "data/logs"
EXPORT_DIR = "data/exports"
