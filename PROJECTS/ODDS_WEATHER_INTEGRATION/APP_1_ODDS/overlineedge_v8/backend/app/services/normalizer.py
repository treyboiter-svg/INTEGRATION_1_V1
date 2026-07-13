"""normalizer.py — OverlineEdge v8 (Python 3.14 compatible)"""
from __future__ import annotations
import re
from difflib import SequenceMatcher
import pytz

ET_TZ = pytz.timezone("America/New_York")

_RECORD_RE  = re.compile(r"\s*\d{1,3}[-\u2013]\d{1,3}(?:[-\u2013]\d{1,3})?\s*$")
_RANK_RE    = re.compile(r"^#?\s*\d+\s+")

def clean_raw(raw: str, sport: str = "") -> str:
    s = raw.strip()
    s = _RECORD_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()

def _clean_college(raw: str) -> str:
    return _RANK_RE.sub("", raw.strip()).strip()

_CANON: dict = {}

def _add(sport, canonical, *aliases):
    for a in aliases:
        _CANON[(a.lower(), sport)] = canonical
        if (a.lower(), None) not in _CANON:
            _CANON[(a.lower(), None)] = canonical

# NFL
_add("nfl","Cardinals","Arizona Cardinals","Cardinals")
_add("nfl","Falcons","Atlanta Falcons","Falcons")
_add("nfl","Ravens","Baltimore Ravens","Ravens")
_add("nfl","Bills","Buffalo Bills","Bills")
_add("nfl","Panthers","Carolina Panthers","Carolina Panthers")
_add("nfl","Bears","Chicago Bears","Bears")
_add("nfl","Bengals","Cincinnati Bengals","Bengals")
_add("nfl","Browns","Cleveland Browns","Browns")
_add("nfl","Cowboys","Dallas Cowboys","Cowboys")
_add("nfl","Broncos","Denver Broncos","Broncos")
_add("nfl","Lions","Detroit Lions","Lions")
_add("nfl","Packers","Green Bay Packers","Packers")
_add("nfl","Texans","Houston Texans","Texans")
_add("nfl","Colts","Indianapolis Colts","Colts")
_add("nfl","Jaguars","Jacksonville Jaguars","Jaguars")
_add("nfl","Chiefs","Kansas City Chiefs","Chiefs")
_add("nfl","Raiders","Las Vegas Raiders","Raiders","Oakland Raiders")
_add("nfl","Chargers","Los Angeles Chargers","LA Chargers","Chargers")
_add("nfl","Rams","Los Angeles Rams","LA Rams","Rams")
_add("nfl","Dolphins","Miami Dolphins","Dolphins")
_add("nfl","Vikings","Minnesota Vikings","Vikings")
_add("nfl","Patriots","New England Patriots","Patriots")
_add("nfl","Saints","New Orleans Saints","Saints")
_add("nfl","Giants","New York Giants","NY Giants")
_add("nfl","Jets","New York Jets","NY Jets")
_add("nfl","Eagles","Philadelphia Eagles","Eagles")
_add("nfl","Steelers","Pittsburgh Steelers","Steelers")
_add("nfl","49ers","San Francisco 49ers","49ers","Niners")
_add("nfl","Seahawks","Seattle Seahawks","Seahawks")
_add("nfl","Buccaneers","Tampa Bay Buccaneers","Buccaneers","Bucs")
_add("nfl","Titans","Tennessee Titans","Titans")
_add("nfl","Commanders","Washington Commanders","Commanders")
# NBA
_add("nba","Hawks","Atlanta Hawks","Hawks")
_add("nba","Celtics","Boston Celtics","Celtics")
_add("nba","Nets","Brooklyn Nets","Nets")
_add("nba","Hornets","Charlotte Hornets","Hornets")
_add("nba","Bulls","Chicago Bulls","Bulls")
_add("nba","Cavaliers","Cleveland Cavaliers","Cavaliers","Cavs")
_add("nba","Mavericks","Dallas Mavericks","Mavericks","Mavs")
_add("nba","Nuggets","Denver Nuggets","Nuggets")
_add("nba","Pistons","Detroit Pistons","Pistons")
_add("nba","Warriors","Golden State Warriors","Warriors")
_add("nba","Rockets","Houston Rockets","Rockets")
_add("nba","Pacers","Indiana Pacers","Pacers")
_add("nba","Clippers","Los Angeles Clippers","LA Clippers","Clippers")
_add("nba","Lakers","Los Angeles Lakers","LA Lakers","Lakers")
_add("nba","Grizzlies","Memphis Grizzlies","Grizzlies")
_add("nba","Heat","Miami Heat","Heat")
_add("nba","Bucks","Milwaukee Bucks","Bucks")
_add("nba","Timberwolves","Minnesota Timberwolves","Timberwolves","Wolves")
_add("nba","Pelicans","New Orleans Pelicans","Pelicans")
_add("nba","Knicks","New York Knicks","Knicks")
_add("nba","Thunder","Oklahoma City Thunder","Thunder","OKC")
_add("nba","Magic","Orlando Magic","Magic")
_add("nba","76ers","Philadelphia 76ers","76ers","Sixers")
_add("nba","Suns","Phoenix Suns","Suns")
_add("nba","Trail Blazers","Portland Trail Blazers","Trail Blazers","Blazers")
_add("nba","Kings","Sacramento Kings","Sacramento Kings")
_add("nba","Spurs","San Antonio Spurs","Spurs","San Antonio Spurs")
_add("nba","Raptors","Toronto Raptors","Raptors")
_add("nba","Jazz","Utah Jazz","Jazz")
_add("nba","Wizards","Washington Wizards","Wizards")
# MLB
_add("mlb","Diamondbacks","Arizona Diamondbacks","Diamondbacks","D-backs")
_add("mlb","Braves","Atlanta Braves","Braves")
_add("mlb","Orioles","Baltimore Orioles","Orioles")
_add("mlb","Red Sox","Boston Red Sox","Red Sox")
_add("mlb","Cubs","Chicago Cubs","Cubs")
_add("mlb","White Sox","Chicago White Sox","White Sox")
_add("mlb","Reds","Cincinnati Reds","Reds")
_add("mlb","Guardians","Cleveland Guardians","Guardians")
_add("mlb","Rockies","Colorado Rockies","Rockies")
_add("mlb","Tigers","Detroit Tigers","Tigers")
_add("mlb","Astros","Houston Astros","Astros")
_add("mlb","Royals","Kansas City Royals","Royals")
_add("mlb","Angels","Los Angeles Angels","LA Angels","Angels")
_add("mlb","Dodgers","Los Angeles Dodgers","LA Dodgers","Dodgers")
_add("mlb","Marlins","Miami Marlins","Marlins")
_add("mlb","Brewers","Milwaukee Brewers","Brewers")
_add("mlb","Twins","Minnesota Twins","Twins")
_add("mlb","Mets","New York Mets","Mets")
_add("mlb","Yankees","New York Yankees","Yankees")
_add("mlb","Athletics","Oakland Athletics","Athletics","Sacramento Athletics","A's")
_add("mlb","Phillies","Philadelphia Phillies","Phillies")
_add("mlb","Pirates","Pittsburgh Pirates","Pirates")
_add("mlb","Padres","San Diego Padres","Padres")
_add("mlb","Giants","San Francisco Giants","SF Giants")
_add("mlb","Mariners","Seattle Mariners","Mariners")
_add("mlb","Cardinals","St. Louis Cardinals","St Louis Cardinals","STL Cardinals")
_add("mlb","Rays","Tampa Bay Rays","Rays")
_add("mlb","Rangers","Texas Rangers","Rangers")
_add("mlb","Blue Jays","Toronto Blue Jays","Blue Jays")
_add("mlb","Nationals","Washington Nationals","Nationals")
# NHL
_add("nhl","Ducks","Anaheim Ducks","Ducks")
_add("nhl","Utah HC","Utah Hockey Club","Utah HC")
_add("nhl","Bruins","Boston Bruins","Bruins")
_add("nhl","Sabres","Buffalo Sabres","Sabres")
_add("nhl","Flames","Calgary Flames","Flames")
_add("nhl","Hurricanes","Carolina Hurricanes","Hurricanes","Canes")
_add("nhl","Blackhawks","Chicago Blackhawks","Blackhawks")
_add("nhl","Avalanche","Colorado Avalanche","Avalanche","Avs")
_add("nhl","Blue Jackets","Columbus Blue Jackets","Blue Jackets")
_add("nhl","Stars","Dallas Stars","Stars")
_add("nhl","Red Wings","Detroit Red Wings","Red Wings")
_add("nhl","Oilers","Edmonton Oilers","Oilers")
_add("nhl","Panthers","Florida Panthers","Florida Panthers")
_add("nhl","Golden Knights","Vegas Golden Knights","Golden Knights","VGK")
_add("nhl","Kings","Los Angeles Kings","LA Kings")
_add("nhl","Wild","Minnesota Wild","Wild")
_add("nhl","Canadiens","Montreal Canadiens","Canadiens","Habs")
_add("nhl","Predators","Nashville Predators","Predators","Preds")
_add("nhl","Devils","New Jersey Devils","Devils")
_add("nhl","Islanders","New York Islanders","Islanders")
_add("nhl","Senators","Ottawa Senators","Senators","Sens")
_add("nhl","Flyers","Philadelphia Flyers","Flyers")
_add("nhl","Penguins","Pittsburgh Penguins","Penguins","Pens")
_add("nhl","Sharks","San Jose Sharks","Sharks")
_add("nhl","Kraken","Seattle Kraken","Kraken")
_add("nhl","Blues","St. Louis Blues","Blues","St Louis Blues")
_add("nhl","Lightning","Tampa Bay Lightning","Lightning")
_add("nhl","Maple Leafs","Toronto Maple Leafs","Maple Leafs","Leafs")
_add("nhl","Canucks","Vancouver Canucks","Canucks")
_add("nhl","Capitals","Washington Capitals","Capitals","Caps")
_add("nhl","Jets","Winnipeg Jets","Winnipeg Jets")

_TID: dict = {
    ("nfl","Cardinals"):"nfl_cardinals",("nfl","Falcons"):"nfl_falcons",
    ("nfl","Ravens"):"nfl_ravens",("nfl","Bills"):"nfl_bills",
    ("nfl","Panthers"):"nfl_panthers",("nfl","Bears"):"nfl_bears",
    ("nfl","Bengals"):"nfl_bengals",("nfl","Browns"):"nfl_browns",
    ("nfl","Cowboys"):"nfl_cowboys",("nfl","Broncos"):"nfl_broncos",
    ("nfl","Lions"):"nfl_lions",("nfl","Packers"):"nfl_packers",
    ("nfl","Texans"):"nfl_texans",("nfl","Colts"):"nfl_colts",
    ("nfl","Jaguars"):"nfl_jaguars",("nfl","Chiefs"):"nfl_chiefs",
    ("nfl","Raiders"):"nfl_raiders",("nfl","Chargers"):"nfl_chargers",
    ("nfl","Rams"):"nfl_rams",("nfl","Dolphins"):"nfl_dolphins",
    ("nfl","Vikings"):"nfl_vikings",("nfl","Patriots"):"nfl_patriots",
    ("nfl","Saints"):"nfl_saints",("nfl","Giants"):"nfl_giants",
    ("nfl","Jets"):"nfl_jets",("nfl","Eagles"):"nfl_eagles",
    ("nfl","Steelers"):"nfl_steelers",("nfl","49ers"):"nfl_49ers",
    ("nfl","Seahawks"):"nfl_seahawks",("nfl","Buccaneers"):"nfl_buccaneers",
    ("nfl","Titans"):"nfl_titans",("nfl","Commanders"):"nfl_commanders",
    ("nba","Hawks"):"nba_hawks",("nba","Celtics"):"nba_celtics",
    ("nba","Nets"):"nba_nets",("nba","Hornets"):"nba_hornets",
    ("nba","Bulls"):"nba_bulls",("nba","Cavaliers"):"nba_cavaliers",
    ("nba","Mavericks"):"nba_mavericks",("nba","Nuggets"):"nba_nuggets",
    ("nba","Pistons"):"nba_pistons",("nba","Warriors"):"nba_warriors",
    ("nba","Rockets"):"nba_rockets",("nba","Pacers"):"nba_pacers",
    ("nba","Clippers"):"nba_clippers",("nba","Lakers"):"nba_lakers",
    ("nba","Grizzlies"):"nba_grizzlies",("nba","Heat"):"nba_heat",
    ("nba","Bucks"):"nba_bucks",("nba","Timberwolves"):"nba_timberwolves",
    ("nba","Pelicans"):"nba_pelicans",("nba","Knicks"):"nba_knicks",
    ("nba","Thunder"):"nba_thunder",("nba","Magic"):"nba_magic",
    ("nba","76ers"):"nba_76ers",("nba","Suns"):"nba_suns",
    ("nba","Trail Blazers"):"nba_trail_blazers",("nba","Kings"):"nba_kings",
    ("nba","Spurs"):"nba_spurs",("nba","Raptors"):"nba_raptors",
    ("nba","Jazz"):"nba_jazz",("nba","Wizards"):"nba_wizards",
    ("mlb","Diamondbacks"):"mlb_diamondbacks",("mlb","Braves"):"mlb_braves",
    ("mlb","Orioles"):"mlb_orioles",("mlb","Red Sox"):"mlb_red_sox",
    ("mlb","Cubs"):"mlb_cubs",("mlb","White Sox"):"mlb_white_sox",
    ("mlb","Reds"):"mlb_reds",("mlb","Guardians"):"mlb_guardians",
    ("mlb","Rockies"):"mlb_rockies",("mlb","Tigers"):"mlb_tigers",
    ("mlb","Astros"):"mlb_astros",("mlb","Royals"):"mlb_royals",
    ("mlb","Angels"):"mlb_angels",("mlb","Dodgers"):"mlb_dodgers",
    ("mlb","Marlins"):"mlb_marlins",("mlb","Brewers"):"mlb_brewers",
    ("mlb","Twins"):"mlb_twins",("mlb","Mets"):"mlb_mets",
    ("mlb","Yankees"):"mlb_yankees",("mlb","Athletics"):"mlb_athletics",
    ("mlb","Phillies"):"mlb_phillies",("mlb","Pirates"):"mlb_pirates",
    ("mlb","Padres"):"mlb_padres",("mlb","Giants"):"mlb_giants",
    ("mlb","Mariners"):"mlb_mariners",("mlb","Cardinals"):"mlb_cardinals",
    ("mlb","Rays"):"mlb_rays",("mlb","Rangers"):"mlb_rangers",
    ("mlb","Blue Jays"):"mlb_blue_jays",("mlb","Nationals"):"mlb_nationals",
    ("nhl","Ducks"):"nhl_ducks",("nhl","Utah HC"):"nhl_utah_hc",
    ("nhl","Bruins"):"nhl_bruins",("nhl","Sabres"):"nhl_sabres",
    ("nhl","Flames"):"nhl_flames",("nhl","Hurricanes"):"nhl_hurricanes",
    ("nhl","Blackhawks"):"nhl_blackhawks",("nhl","Avalanche"):"nhl_avalanche",
    ("nhl","Blue Jackets"):"nhl_blue_jackets",("nhl","Stars"):"nhl_stars",
    ("nhl","Red Wings"):"nhl_red_wings",("nhl","Oilers"):"nhl_oilers",
    ("nhl","Panthers"):"nhl_panthers",("nhl","Golden Knights"):"nhl_golden_knights",
    ("nhl","Kings"):"nhl_kings",("nhl","Wild"):"nhl_wild",
    ("nhl","Canadiens"):"nhl_canadiens",("nhl","Predators"):"nhl_predators",
    ("nhl","Devils"):"nhl_devils",("nhl","Islanders"):"nhl_islanders",
    ("nhl","Senators"):"nhl_senators",("nhl","Flyers"):"nhl_flyers",
    ("nhl","Penguins"):"nhl_penguins",("nhl","Sharks"):"nhl_sharks",
    ("nhl","Kraken"):"nhl_kraken",("nhl","Blues"):"nhl_blues",
    ("nhl","Lightning"):"nhl_lightning",("nhl","Maple Leafs"):"nhl_maple_leafs",
    ("nhl","Canucks"):"nhl_canucks",("nhl","Capitals"):"nhl_capitals",
    ("nhl","Jets"):"nhl_jets",
}

def normalize_team(raw: str, sport: str = "") -> str:
    s  = clean_raw(raw, sport)
    sl = sport.lower() if sport else ""
    if sl in ("ncaaf","ncaab"):
        return _clean_college(s)
    canon = _CANON.get((s.lower(), sl)) or _CANON.get((s.lower(), None))
    if canon: return canon
    candidates = [(k,v) for (k,sp),v in _CANON.items() if sp==sl or sp is None]
    best, best_score = s, 0.0
    for key, val in candidates:
        score = SequenceMatcher(None, s.lower(), key).ratio()
        if score>best_score and score>0.72:
            best_score=score; best=val
    return best

def team_id(canonical: str, sport: str = "") -> str:
    sl  = sport.lower() if sport else ""
    tid = _TID.get((sl, canonical))
    if tid: return tid
    slug   = re.sub(r"[^a-z0-9]+","_",canonical.lower()).strip("_")
    prefix = sl if sl else "unk"
    return f"{prefix}_{slug}"

def match_teams(home, away, candidates, threshold=0.6, sport="") -> str | None:
    home_c = normalize_team(home, sport).lower()
    away_c = normalize_team(away, sport).lower()
    best_key, best_score = None, 0.0
    for cand in candidates:
        cl = cand.lower()
        score = max(
            SequenceMatcher(None, home_c, cl).ratio(),
            SequenceMatcher(None, away_c, cl).ratio(),
            SequenceMatcher(None, f"{away_c} vs {home_c}", cl).ratio(),
            SequenceMatcher(None, f"{home_c} vs {away_c}", cl).ratio(),
        )
        if home_c in cl or away_c in cl: score=max(score,0.75)
        if score>best_score: best_score=score; best_key=cand
    return best_key if best_score>=threshold else None
