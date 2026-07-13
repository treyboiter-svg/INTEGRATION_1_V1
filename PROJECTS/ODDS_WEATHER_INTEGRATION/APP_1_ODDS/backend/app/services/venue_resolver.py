"""Canonical, venue-backed identity resolution for OverlineEdge."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

LEAGUE_ALIASES = {
    "mlb": "MLB", "nfl": "NFL", "nba": "NBA", "nhl": "NHL",
    "ncaaf": "NCAA FBS", "ncaab": "NCAA D1 MBB",
}

NOISE_WORDS = {
    "will", "the", "to", "win", "wins", "beat", "beats", "versus", "vs", "v",
    "game", "match", "matchup", "on", "at", "in", "today", "tomorrow", "yes", "no",
    "moneyline", "ml", "home", "away", "team", "sports", "spread", "total", "over", "under",
}

@dataclass(frozen=True)
class VenueTeam:
    league: str
    team: str
    venue: str
    city: str
    state: str
    lat: float | None
    lon: float | None
    elevation: float | None
    orientation_deg: float | None
    orientation_label: str | None
    orientation_confidence: float | None


def _fold(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _tokens(value: str) -> set[str]:
    return {x for x in _fold(value).split() if x and x not in NOISE_WORDS}


def _camel_split(value: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))


def clean_source_name(raw: str) -> str:
    """Remove records, rankings, and final MLB pitcher/handedness suffixes."""
    s = _camel_split(str(raw or "").strip())
    s = re.sub("[ ]+[A-Za-z.'-]+[ ]*[(][LRlr][)][ ]*$", "", s)
    s = re.sub("[ ]*[0-9]{1,3}[-–][0-9]{1,3}([-–][0-9]{1,3})?[ ]*$", "", s)
    s = re.sub("^#?[ ]*[0-9]+[ ]+", "", s)
    return " ".join(s.split())


class VenueResolver:
    """Resolve every entity against the supplied venue master before joining odds."""
    def __init__(self, workbook: str | Path):
        p = Path(workbook)
        if not p.exists():
            raise FileNotFoundError(f"Venue authority workbook not found: {p}")
        df = pd.read_excel(p, sheet_name="ALL_VENUES")
        required = {"league", "team", "venue", "city", "state"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Venue workbook missing required columns: {sorted(missing)}")
        self.rows: list[VenueTeam] = []
        self.by_league: dict[str, list[VenueTeam]] = {}
        for _, r in df.iterrows():
            row = VenueTeam(
                league=str(r["league"]).strip(), team=str(r["team"]).strip(),
                venue=str(r["venue"]).strip(), city=str(r["city"]).strip(), state=str(r["state"]).strip(),
                lat=self._num(r.get("lat")), lon=self._num(r.get("lon")), elevation=self._num(r.get("elevation")),
                orientation_deg=self._num(r.get("orientation_deg")), orientation_label=self._text(r.get("orientation_label")),
                orientation_confidence=self._num(r.get("orientation_confidence")),
            )
            self.rows.append(row); self.by_league.setdefault(row.league, []).append(row)
        self.aliases = self._build_aliases()

    @staticmethod
    def _num(v):
        return None if pd.isna(v) else float(v)
    @staticmethod
    def _text(v):
        return None if pd.isna(v) else str(v)

    def _build_aliases(self):
        aliases = {}
        token_counts: dict[tuple[str, str], int] = {}
        for row in self.rows:
            for token in _tokens(row.team):
                token_counts[(row.league, token)] = token_counts.get((row.league, token), 0) + 1
        for row in self.rows:
            key = (row.league, _fold(row.team)); aliases[key] = row
            parts = _tokens(row.team)
            if parts:
                aliases[(row.league, " ".join(sorted(parts)))] = row
                team_words = _fold(row.team).split()
                # Last two words identify composite mascots such as Red Sox / White Sox.
                if len(team_words) >= 2:
                    aliases[(row.league, " ".join(team_words[-2:]))] = row
                # A unique mascot token is a deterministic alias; ambiguous city tokens never are.
                for token in parts:
                    if token_counts.get((row.league, token)) == 1:
                        aliases[(row.league, token)] = row
        # Explicit stable aliases not guaranteed by raw feeds.
        manual = {
            ("MLB", "a s"): "Athletics", ("MLB", "oakland athletics"): "Athletics",
            ("MLB", "sacramento athletics"): "Athletics", ("NFL", "niners"): "San Francisco 49ers",
            ("NBA", "cavs"): "Cleveland Cavaliers", ("NBA", "sixers"): "Philadelphia 76ers",
            ("NHL", "habs"): "Montreal Canadiens", ("NHL", "avs"): "Colorado Avalanche",
        }
        for (league, alias), team in manual.items():
            match = next((r for r in self.by_league.get(league, []) if r.team == team), None)
            if match: aliases[(league, _fold(alias))] = match
        return aliases

    def canonical_league(self, sport: str) -> str:
        return LEAGUE_ALIASES.get(str(sport).lower(), str(sport))

    def resolve_team(self, sport: str, raw_name: str, *, min_score: float = 0.78) -> tuple[VenueTeam | None, float, str]:
        league = self.canonical_league(sport); cleaned = clean_source_name(raw_name); folded = _fold(cleaned)
        if not folded: return None, 0.0, cleaned
        direct = self.aliases.get((league, folded))
        if direct: return direct, 1.0, cleaned
        raw_tokens = _tokens(cleaned)
        # Source labels can append one or more pitcher-name words (e.g. "Mets Mc Lean (R)").
        # Accept a league-scoped team alias only if every alias word exists in the label.
        contained = []
        for (alias_league, alias), row in self.aliases.items():
            alias_tokens = set(alias.split())
            if alias_league == league and alias_tokens and alias_tokens <= raw_tokens:
                contained.append((len(alias_tokens), row))
        if contained:
            contained.sort(key=lambda x: x[0], reverse=True)
            return contained[0][1], 1.0, cleaned
        best, best_score = None, 0.0
        for row in self.by_league.get(league, []):
            team_fold = _fold(row.team); team_tokens = _tokens(row.team)
            overlap = len(raw_tokens & team_tokens) / max(1, len(team_tokens))
            ratio = SequenceMatcher(None, folded, team_fold).ratio()
            city_ratio = 1.0 if _fold(row.city) in folded else 0.0
            score = max(overlap, ratio, min(1.0, 0.70 * overlap + 0.30 * city_ratio))
            if score > best_score: best, best_score = row, score
        return (best if best_score >= min_score else None), best_score, cleaned

    def validate_game(self, sport: str, away_raw: str, home_raw: str) -> dict:
        away, away_score, away_clean = self.resolve_team(sport, away_raw)
        home, home_score, home_clean = self.resolve_team(sport, home_raw)
        accepted = bool(away and home and away.team != home.team)
        # A legitimate non-neutral home game must resolve to an authoritative venue row.
        reason = "accepted" if accepted else "unresolved_team_or_same_team"
        return {
            "accepted": accepted, "reason": reason,
            "away": away, "home": home,
            "away_clean": away_clean, "home_clean": home_clean,
            "away_score": round(away_score, 4), "home_score": round(home_score, 4),
        }

    def match_market(self, game: dict, candidates: Iterable[str]) -> tuple[str | None, float, str]:
        """Require BOTH canonical teams in a prediction-market title before fuzzy fallback."""
        away, home = game["away"], game["home"]
        away_tokens, home_tokens = _tokens(away.team), _tokens(home.team)
        best_key, best_score, method = None, 0.0, "none"
        for candidate in candidates:
            ct = _tokens(candidate)
            away_hit = len(away_tokens & ct) / max(1, len(away_tokens))
            home_hit = len(home_tokens & ct) / max(1, len(home_tokens))
            venue_hit = 1.0 if _fold(home.city) in _fold(candidate) or _fold(home.venue) in _fold(candidate) else 0.0
            # Both teams are mandatory. City/venue can strengthen, never substitute for a team.
            if away_hit >= 0.5 and home_hit >= 0.5:
                score = 0.45 * away_hit + 0.45 * home_hit + 0.10 * venue_hit
                if score > best_score: best_key, best_score, method = candidate, score, "both_canonical_teams"
        if best_score >= 0.78:
            return best_key, round(best_score, 4), method
        return None, round(best_score, 4), "rejected_missing_both_teams"


def default_workbook_path() -> Path:
    return Path(__file__).resolve().parents[3] / "US_SPORTS_VENUES_MASTER_CORRECTED_V2.xlsx"
