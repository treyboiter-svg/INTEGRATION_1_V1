#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import re
import sys
import webbrowser
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

VERSION = "36.0.0"
MLB = "https://statsapi.mlb.com/api/v1"
SAVANT = "https://baseballsavant.mlb.com/statcast_search/csv"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
R_D = 287.05
R_V = 461.495
G = 9.80665
RHO_REF = 1.2041
LOG = logging.getLogger("mlb_pitch_environment_v33")

OUTDOOR_CLASSES = {"OUTDOOR"}
FIXED_CLASSES = {"FIXEDENCLOSED", "FIXED_ENCLOSED", "FIXED ENCLOSED"}
RETRACTABLE_CLASSES = {"RETRACTABLE"}

BRANDING = {
    "Daikin Park": "Minute Maid Park",
    "UNIQLO Field at Dodger Stadium": "Dodger Stadium",
    "Rate Field": "Guaranteed Rate Field",
    "loanDepot Park": "loanDepot park",
    "T Mobile Park": "T-Mobile Park",
    "TMobile Park": "T-Mobile Park",
}

ROOF_GENERIC_SOURCES = {
    "sportspredictapp": {"url": "https://sportspredictapp.com/mlb/weather", "mode": "CARD"},
    "weathermlb": {"url": "https://weathermlb.com/", "mode": "CARD"},
    "rotowire": {"url": "https://www.rotowire.com/baseball/weather.php", "mode": "PROSE"},
}

VENUE_DEDICATED_SOURCES = {
    "Rogers Centre": {"isthedomeopen": {"url": "https://isthedomeopen.com/", "mode": "SIMPLE"}},
    "American Family Field": {"istheroofopen": {"url": "https://istheroofopen.com/american-family-field/", "mode": "SIMPLE"}},
    "Chase Field": {"istheroofopen": {"url": "https://istheroofopen.com/chase-field/", "mode": "SIMPLE"}},
    "Globe Life Field": {"istheroofopen": {"url": "https://istheroofopen.com/globe-life-field/", "mode": "SIMPLE"}},
    "Minute Maid Park": {"istheroofopen": {"url": "https://istheroofopen.com/minute-maid-park/", "mode": "SIMPLE"}},
    "loanDepot park": {"istheroofopen": {"url": "https://istheroofopen.com/loandepot-park/", "mode": "SIMPLE"}},
    "T-Mobile Park": {"istheroofopen": {"url": "https://istheroofopen.com/t-mobile-park/", "mode": "SIMPLE"}},
}

OPEN_RX = re.compile(
    r"\b(?:roof|dome)\b[^.]{0,90}\b(?:is|will\s+(?:likely\s+)?be|expected\s+to\s+be|likely|projected\s+to\s+be)\b[^.]{0,30}\bopen\b"
    r"|\bopen\s+(?:roof|dome)\b|\broof\s*[:\-]?\s*open\b|\bwill\s+(?:likely\s+)?open\b|\bopen[- ]air\b|\bconditions?\s+will\s+favor\s+an?\s+open\b",
    re.I,
)
CLOSED_RX = re.compile(
    r"\b(?:roof|dome)\b[^.]{0,90}\b(?:is|will\s+(?:likely\s+)?be|expected\s+to\s+be|likely|projected\s+to\s+be)\b[^.]{0,30}\bclosed\b"
    r"|\bclosed\s+(?:roof|dome)\b|\broof\s*[:\-]?\s*closed\b|\bkeep(?:ing)?\s+the\s+roof[^.]{0,60}\bclosed\b|\bwill\s+(?:likely\s+)?close\b|\bclosed\s+dome\b",
    re.I,
)
ROOF_KEYWORD_RX = re.compile(r"\broof\b|\bdome\b", re.I)
BOILERPLATE_DOME_RX = re.compile(r"\bdomed stadium\b|\binside a domed stadium\b", re.I)


@dataclass
class Finding:
    severity: str
    code: str
    entity: str
    message: str
    action: str


class Health:
    COLUMNS = ["severity", "code", "entity", "message", "action"]

    def __init__(self) -> None:
        self.rows: list[Finding] = []

    def add(self, severity: str, code: str, entity: Any = "", message: str = "", action: str = "") -> None:
        self.rows.append(Finding(severity, code, str(entity), message, action))
        fn = LOG.error if severity == "FATAL" else (LOG.warning if severity == "WARN" else LOG.info)
        fn("%s %s [%s] %s", severity, code, entity, message)

    def fatal(self) -> bool:
        return any(x.severity == "FATAL" for x in self.rows)

    def frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame(columns=self.COLUMNS)
        return pd.DataFrame([asdict(x) for x in self.rows], columns=self.COLUMNS)


def safe_read_csv(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if (not path.exists()) or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns or [])
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame(columns=columns or [])


def write_csv_contract(df: pd.DataFrame, path: Path, columns: list[str] | None = None) -> None:
    if columns is not None:
        if df.empty:
            pd.DataFrame(columns=columns).to_csv(path, index=False)
            return
        work = df.copy()
        for c in columns:
            if c not in work.columns:
                work[c] = pd.NA
        work[columns].to_csv(path, index=False)
        return
    if df.empty:
        pd.DataFrame(columns=list(df.columns)).to_csv(path, index=False)
    else:
        df.to_csv(path, index=False)


def norm(x: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(x or "").lower()).strip()


def col(x: Any) -> str:
    return norm(x).replace(" ", "")


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_venue(x: str) -> str:
    return BRANDING.get(str(x), str(x))


def num(x: Any):
    return pd.to_numeric(x, errors="coerce")


def first(df: pd.DataFrame, *names: str) -> str | None:
    return next((n for n in names if n in df.columns), None)


def mascot(name: str) -> str:
    parts = norm(name).split()
    return parts[-1] if parts else ""


def clean_page(text: str) -> str:
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    stripped = re.sub(r"<style\b[^>]*>.*?</style>", " ", stripped, flags=re.I | re.S)
    stripped = re.sub(r"<[^>]+>", " ", html.unescape(stripped))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=5, connect=5, read=5, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": f"Mozilla/5.0 MLB-Pitch-Environment-V{VERSION}",
        "Accept": "text/html,application/json,text/csv,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    return s


def get_json(s: requests.Session, url: str, health: Health, code: str, params: dict[str, Any] | None = None, timeout: int = 45):
    try:
        r = s.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        health.add("WARN", code, url, f"{type(e).__name__}: {e}", "Continue with next fallback.")
        return None


def get_text(s: requests.Session, url: str, raw: Path, label: str, health: Health):
    try:
        r = s.get(url, timeout=45)
        r.raise_for_status()
        p = raw / f"{label}.html"
        p.write_text(r.text, encoding="utf-8")
        return r.text, p, r.status_code
    except Exception as e:
        health.add("WARN", "TEXT_FETCH_FAILED", label, f"{type(e).__name__}: {e}", "Source abstains.")
        return "", None, None


def load_parks(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    d.columns = [col(x) for x in d.columns]
    need = {"venuename", "team", "lat", "lon", "ballparkelevationm", "rooftype", "homeplatebearingdeg"}
    miss = need - set(d.columns)
    if miss:
        raise ValueError(f"Park reference missing columns: {sorted(miss)}")
    for x in ("lat", "lon", "ballparkelevationm", "homeplatebearingdeg", "stationelevationm"):
        if x in d.columns:
            d[x] = num(d[x])
    d["roofclass"] = d["rooftype"].map(lambda x: norm(x).replace(" ", "").upper())
    valid = OUTDOOR_CLASSES | FIXED_CLASSES | RETRACTABLE_CLASSES
    bad = d.loc[~d["roofclass"].isin(valid), "rooftype"].tolist()
    if bad:
        raise ValueError(f"Unsupported roof classes: {bad}")
    return d


def schedule(s: requests.Session, day: str, health: Health) -> pd.DataFrame:
    j = get_json(s, f"{MLB}/schedule", health, "SCHEDULE_FETCH_FAILED", {"sportId": 1, "date": day, "hydrate": "probablePitcher,venue,team"})
    rows = []
    for date_block in (j or {}).get("dates", []):
        for game in date_block.get("games", []):
            if game.get("gameType") != "R":
                continue
            teams = game["teams"]
            for side in ("away", "home"):
                team = teams[side]
                p = team.get("probablePitcher") or {}
                rows.append({
                    "game_pk": game.get("gamePk"),
                    "game_datetime_utc": game.get("gameDate"),
                    "venue_name_api": game.get("venue", {}).get("name"),
                    "home_team": teams["home"]["team"].get("name"),
                    "away_team": teams["away"]["team"].get("name"),
                    "home_team_id": teams["home"]["team"].get("id"),
                    "away_team_id": teams["away"]["team"].get("id"),
                    "side": side,
                    "team": team["team"].get("name"),
                    "team_id": team["team"].get("id"),
                    "pitcher_id": p.get("id"),
                    "pitcher_name": p.get("fullName"),
                    "pitcher_resolution_method": "MLB_SCHEDULE" if p.get("id") else "UNRESOLVED",
                })
    return pd.DataFrame(rows)


def join_parks(games: pd.DataFrame, parks: pd.DataFrame, health: Health) -> pd.DataFrame:
    rows = []
    for _, g in games.iterrows():
        x = parks[parks.venuename.map(norm).eq(norm(canonical_venue(g.venue_name_api)))]
        method = "EXACT_VENUE_NAME"
        if len(x) != 1:
            x = parks[parks.team.map(norm).eq(norm(g.home_team))]
            method = "EXACT_HOME_TEAM_FALLBACK"
        if len(x) != 1:
            health.add("FATAL", "MISSING_VERIFIED_PARK", g.game_pk, f"venue={g.venue_name_api}; home={g.home_team}", "Fix park reference or branding alias.")
            continue
        z = g.to_dict()
        z.update(x.iloc[0].to_dict())
        z["park_join_method"] = method
        rows.append(z)
    return pd.DataFrame(rows)


def svp(tc: float) -> float:
    return 610.94 * math.exp(17.625 * tc / (tc + 243.04))


def density(tc: float, rh: float, p_hpa: float) -> float:
    e = max(0.0, min(1.0, rh / 100.0)) * svp(tc)
    tk = tc + 273.15
    pa = p_hpa * 100.0
    return (pa - e) / (R_D * tk) + e / (R_V * tk)


def barometric_to_elevation(p_hpa: float, source_elev_m: float, venue_elev_m: float, tc: float) -> float:
    return p_hpa * math.exp(-G * (venue_elev_m - source_elev_m) / (R_D * (tc + 273.15)))


def wetbulb(tc: float, rh: float) -> float:
    return tc * math.atan(.151977 * math.sqrt(rh + 8.313659)) + math.atan(tc + rh) - math.atan(rh - 1.676331) + .00391838 * rh ** 1.5 * math.atan(.023101 * rh) - 4.686035


def ambient(s: requests.Session, row: pd.Series, raw: Path, health: Health) -> dict[str, Any]:
    q = {"latitude": row.lat, "longitude": row.lon, "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m", "timezone": "UTC", "forecast_days": 3}
    j = get_json(s, OPEN_METEO, health, "AMBIENT_FETCH_FAILED", q)
    if not j:
        return {}
    (raw / f"ambient_{row.game_pk}.json").write_text(json.dumps(j, indent=2), encoding="utf-8")
    h = j.get("hourly", {})
    ts = pd.to_datetime(h.get("time", []), utc=True)
    if len(ts) == 0:
        return {}
    i = int(np.argmin(abs(ts - pd.to_datetime(row.game_datetime_utc, utc=True))))
    try:
        tc = float(h["temperature_2m"][i]); rh = float(h["relative_humidity_2m"][i]); p = float(h["surface_pressure"][i]); ws = float(h["wind_speed_10m"][i]); wd = float(h["wind_direction_10m"][i]); rho = density(tc, rh, p)
        return {"ambient_temperature_c": tc, "ambient_relative_humidity_pct": rh, "ambient_surface_pressure_hpa": p, "ambient_wind_speed_kmh": ws, "ambient_wind_direction_deg": wd, "ambient_air_density_kg_m3": rho, "ambient_density_ratio_to_ref": rho / RHO_REF, "ambient_wetbulb_c": wetbulb(tc, rh)}
    except Exception as e:
        health.add("WARN", "AMBIENT_SCHEMA_INVALID", row.game_pk, str(e), "Continue with remaining games.")
        return {}


def wind_vectors(wind_speed_kmh: float, wind_direction_deg: float, homeplate_bearing_deg: float) -> dict[str, float]:
    rel = (float(wind_direction_deg) - float(homeplate_bearing_deg)) % 360.0
    rad = math.radians(rel)
    out_to_cf = wind_speed_kmh * math.cos(rad)
    cross = wind_speed_kmh * math.sin(rad)
    return {"wind_to_cf_angle_deg": rel, "wind_out_to_cf_kmh": out_to_cf, "wind_cross_kmh": cross}


def outdoor_branch(row: pd.Series, a: dict[str, Any]) -> dict[str, Any]:
    w = wind_vectors(a["ambient_wind_speed_kmh"], a["ambient_wind_direction_deg"], row.homeplatebearingdeg)
    return {
        "branch": "OUTDOOR",
        "air_environment_basis": "LOCAL_OUTDOOR_AMBIENT",
        "temperature_humidity_basis": "OPEN_METEO_FORECAST",
        "pressure_basis": "LOCAL_SURFACE_PRESSURE",
        "temperature_c": a["ambient_temperature_c"],
        "relative_humidity_pct": a["ambient_relative_humidity_pct"],
        "pressure_hpa": a["ambient_surface_pressure_hpa"],
        "air_density_kg_m3": a["ambient_air_density_kg_m3"],
        "density_ratio_to_ref": a["ambient_density_ratio_to_ref"],
        "density_uncertainty_kg_m3": 0.01,
        "external_wind_speed_kmh": a["ambient_wind_speed_kmh"],
        "external_wind_direction_deg": a["ambient_wind_direction_deg"],
        **w,
        "wind_model_status": "APPLICABLE_OUTDOOR",
    }


def enclosed_branch(row: pd.Series, a: dict[str, Any]) -> dict[str, Any]:
    tc, rh = a["ambient_temperature_c"], a["ambient_relative_humidity_pct"]
    src = float(row.stationelevationm) if "stationelevationm" in row.index and pd.notna(row.get("stationelevationm")) else float(row.ballparkelevationm)
    p = barometric_to_elevation(a["ambient_surface_pressure_hpa"], src, float(row.ballparkelevationm), tc)
    rho = density(tc, rh, p)
    return {
        "branch": "ENCLOSED",
        "air_environment_basis": "INDOOR_MODELED_PRESSURE_ANCHORED",
        "temperature_humidity_basis": "OUTDOOR_AMBIENT_PROXY_HVAC_UNMEASURED",
        "pressure_basis": "LOCAL_BAROMETRY_ELEVATION_CORRECTED",
        "temperature_c": tc,
        "relative_humidity_pct": rh,
        "pressure_hpa": p,
        "air_density_kg_m3": rho,
        "density_ratio_to_ref": rho / RHO_REF,
        "density_uncertainty_kg_m3": 0.02,
        "external_wind_speed_kmh": 0.0,
        "external_wind_direction_deg": np.nan,
        "wind_to_cf_angle_deg": np.nan,
        "wind_out_to_cf_kmh": 0.0,
        "wind_cross_kmh": 0.0,
        "wind_model_status": "ZERO_EXTERNAL_WIND_ENCLOSED",
    }


def vote_rotowire(text: str, row: pd.Series):
    t = clean_page(text)
    keys = [norm(row.home_team), norm(row.away_team), norm(row.venuename), mascot(row.home_team), mascot(row.away_team)]
    hits = [m.start() for key in keys if key for m in re.finditer(re.escape(key), t)]
    votes = []
    found_identity = False
    for i in hits:
        card = t[max(0, i - 700):i + 1900]
        identity = ((norm(row.home_team) in card and norm(row.away_team) in card) or (mascot(row.home_team) in card and mascot(row.away_team) in card) or (norm(row.venuename) in card and (norm(row.home_team) in card or norm(row.away_team) in card)))
        if not identity:
            continue
        found_identity = True
        o, c = bool(OPEN_RX.search(card)), bool(CLOSED_RX.search(card))
        if o ^ c:
            votes.append("OPEN" if o else "CLOSED")
    if votes and len(set(votes)) == 1:
        status = "EXPLICIT_ROOF_NARRATIVE"
        if BOILERPLATE_DOME_RX.search(t):
            status += "_OVERRIDES_BOILERPLATE"
        return votes[0], status
    if found_identity:
        return None, "IDENTITY_FOUND_NO_UNAMBIGUOUS_ROOF_LANGUAGE"
    if ROOF_KEYWORD_RX.search(t):
        return None, "NO_IDENTITY_MATCH_ROOF_LANGUAGE_ELSEWHERE"
    return None, "NO_ROOF_LANGUAGE"


def vote_card(text: str, row: pd.Series):
    t = clean_page(text)
    keys = [norm(row.home_team), norm(row.away_team), norm(row.venuename), mascot(row.home_team), mascot(row.away_team)]
    for key in [k for k in keys if k]:
        for m in re.finditer(re.escape(key), t):
            card = t[max(0, m.start() - 220):m.start() + 520]
            if re.search(r"\broof\s+closed\b|\bclosed\s+roof\b|\blikely\s+closed\b|\bwill\s+be\s+closed\b", card, re.I):
                return "CLOSED", "STRUCTURED_CARD_CLOSED"
            if re.search(r"\broof\s+open\b|\bopen\s+roof\b|\blikely\s+open\b|\bwill\s+be\s+open\b", card, re.I):
                return "OPEN", "STRUCTURED_CARD_OPEN"
    return None, "NO_STRUCTURED_MATCH"


def vote_simple(text: str, row: pd.Series):
    t = clean_page(text)
    if re.search(r"\broof\s+open\b|\bstatus\s+open\b", t, re.I) and not re.search(r"\broof\s+closed\b|\bstatus\s+closed\b", t, re.I):
        return "OPEN", "SINGLE_VENUE_EXPLICIT"
    if re.search(r"\broof\s+closed\b|\bstatus\s+closed\b", t, re.I) and not re.search(r"\broof\s+open\b|\bstatus\s+open\b", t, re.I):
        return "CLOSED", "SINGLE_VENUE_EXPLICIT"
    return None, "SINGLE_VENUE_NO_UNAMBIGUOUS_STATE"


def parse_roof(text: str, row: pd.Series, mode: str):
    if mode == "PROSE":
        return vote_rotowire(text, row)
    if mode == "CARD":
        return vote_card(text, row)
    if mode == "SIMPLE":
        return vote_simple(text, row)
    return None, "UNSUPPORTED_MODE"


def resolve_roof_state(s: requests.Session, row: pd.Series, raw: Path, health: Health, cache: dict[str, Any], evidence_rows: list[dict[str, Any]]):
    if row.roofclass in OUTDOOR_CLASSES:
        return "NOT_APPLICABLE", "STRUCTURAL_OUTDOOR", {}
    if row.roofclass in FIXED_CLASSES:
        return "FIXED_CLOSED", "STRUCTURAL_FIXED_ENCLOSED", {}
    dedicated = VENUE_DEDICATED_SOURCES.get(row.venuename, {})
    all_sources = {**ROOF_GENERIC_SOURCES, **dedicated}
    votes: dict[str, Any] = {}
    for source, meta in all_sources.items():
        if meta["url"] not in cache:
            cache[meta["url"]] = get_text(s, meta["url"], raw, f"roof_{norm(source)}", health)
        text, p, http_status = cache[meta["url"]]
        vote, status = parse_roof(text, row, meta["mode"])
        votes[source] = vote
        evidence_rows.append({
            "game_pk": row.game_pk,
            "venue_name": row.venuename,
            "source": source,
            "scope": "SINGLE_VENUE" if source in dedicated else "MULTI_GAME",
            "url": meta["url"],
            "vote": vote,
            "parser_status": status,
            "http_status": http_status,
            "page_char_length": len(text or ""),
            "identity_or_venue_found": bool(vote or status not in {"NO_ROOF_LANGUAGE", "NO_STRUCTURED_MATCH"}),
            "roof_keyword_found": bool(ROOF_KEYWORD_RX.search(clean_page(text or ""))),
            "raw_file": str(p) if p else None,
        })
    dedicated_votes = [v for k, v in votes.items() if k in dedicated and v]
    if dedicated_votes and len(set(dedicated_votes)) == 1:
        return dedicated_votes[0], "DEDICATED_SINGLE_VENUE_SOURCE_DECISIVE", votes
    generic_votes = [v for k, v in votes.items() if k in ROOF_GENERIC_SOURCES and v]
    if generic_votes.count("OPEN") >= 2 and generic_votes.count("CLOSED") == 0:
        return "OPEN", "TWO_GENERIC_SOURCE_CONSENSUS", votes
    if generic_votes.count("CLOSED") >= 2 and generic_votes.count("OPEN") == 0:
        return "CLOSED", "TWO_GENERIC_SOURCE_CONSENSUS", votes
    health.add("INFO", "RETRACTABLE_ROOF_CONDITIONAL", row.game_pk, f"votes={votes}", "Emit OUTDOOR and ENCLOSED scenario branches; this is modeled conditionality, not a failure.")
    return "UNRESOLVED_RETRACTABLE_ONLY", "NO_DECISIVE_SOURCE_TWO_BRANCH_MODEL", votes


def extract_probable_from_live_feed(js: dict[str, Any], side: str) -> tuple[Any, Any, Any]:
    gd = (js or {}).get("gameData", {})
    side_block = ((gd.get("teams", {}) or {}).get(side) or {}) if isinstance(gd, dict) else {}
    p = side_block.get("probablePitcher") or {}
    if p.get("id"):
        return p.get("id"), p.get("fullName"), "MLB_GAME_FEED"
    if p.get("fullName"):
        return None, p.get("fullName"), "MLB_GAME_FEED_NAME_ONLY"
    return None, None, None


def extract_player_pool_from_live_feed(js: dict[str, Any]) -> list[dict[str, Any]]:
    gd = (js or {}).get("gameData", {})
    players = gd.get("players", {}) if isinstance(gd, dict) else {}
    pool = []
    if isinstance(players, dict):
        for v in players.values():
            if not isinstance(v, dict):
                continue
            pool.append({
                "id": v.get("id"),
                "fullName": v.get("fullName"),
                "primaryPosition": ((v.get("primaryPosition") or {}).get("code") if isinstance(v.get("primaryPosition"), dict) else None),
            })
    return pool


def candidate_team_pitchers_from_live_feed(js: dict[str, Any], team_id: Any) -> list[dict[str, Any]]:
    pool = extract_player_pool_from_live_feed(js)
    out = []
    box = (js or {}).get("liveData", {}).get("boxscore", {}).get("teams", {})
    for side in ("away", "home"):
        block = box.get(side, {}) if isinstance(box, dict) else {}
        if str(((block.get("team") or {}).get("id"))) != str(team_id):
            continue
        for pid in (block.get("pitchers") or []):
            key = f"ID{pid}"
            hit = next((p for p in pool if str(p.get("id")) == str(pid)), None)
            if hit:
                out.append(hit)
    return out


def search_people_exact(s: requests.Session, full_name: str) -> list[dict[str, Any]]:
    hits = []
    for endpoint, params in [
        (f"{MLB}/people/search", {"names": full_name}),
        (f"{MLB}/sports/1/players", {"season": date.today().year}),
    ]:
        try:
            r = s.get(endpoint, params=params, timeout=25)
            r.raise_for_status()
            js = r.json()
            people = js.get("people") or []
            if isinstance(people, list):
                hits.extend([p for p in people if norm(p.get("fullName")) == norm(full_name)])
        except Exception:
            continue
    unique = {}
    for h in hits:
        if h.get("id") is not None:
            unique[h["id"]] = h
    return list(unique.values())


def official_probables_text(s: requests.Session, day: str, raw: Path, health: Health) -> str:
    text, _, _ = get_text(s, f"https://www.mlb.com/probable-pitchers/{day}", raw, f"official_probables_{day}", health)
    return text


def infer_name_candidates_from_text(text: str, row: pd.Series) -> list[str]:
    t = clean_page(text)
    keys = [norm(row.home_team), norm(row.away_team), norm(row.venuename), mascot(row.home_team), mascot(row.away_team)]
    found = []
    for key in [k for k in keys if k]:
        for m in re.finditer(re.escape(key), t):
            card = t[max(0, m.start() - 350):m.start() + 1200]
            if norm(row.home_team) not in card or norm(row.away_team) not in card:
                continue
            tokens = re.findall(r"\b[a-z]+\s+[a-z]+\b", card)
            for tok in tokens:
                if tok in {norm(row.home_team), norm(row.away_team), norm(row.venuename), mascot(row.home_team), mascot(row.away_team)}:
                    continue
                if len(tok.split()) != 2:
                    continue
                name = " ".join(x.capitalize() for x in tok.split())
                if name not in found:
                    found.append(name)
    return found[:10]


def resolve_starters(s: requests.Session, joined: pd.DataFrame, raw: Path, health: Health) -> pd.DataFrame:
    out = joined.copy()
    official = official_probables_text(s, str(pd.to_datetime(out.game_datetime_utc.iloc[0]).date()), raw, health) if not out.empty else ""
    weather_html, _, _ = get_text(s, "https://weathermlb.com/", raw, "weathermlb_probables", health)
    roto_html, _, _ = get_text(s, "https://www.rotowire.com/baseball/weather.php", raw, "rotowire_probables", health)
    public_text = "\n".join([official, weather_html, roto_html])

    live_feed_cache: dict[int, dict[str, Any]] = {}

    for game_pk, block in out.groupby("game_pk"):
        js = get_json(s, f"{MLB}.1/game/{int(game_pk)}/feed/live", health, "GAME_FEED_FETCH_FAILED") or {}
        live_feed_cache[int(game_pk)] = js
        for idx, row in block.iterrows():
            if pd.notna(row.pitcher_id) and str(row.pitcher_name or "").strip():
                continue
            pid, pname, method = extract_probable_from_live_feed(js, row.side)
            if pid is not None:
                out.at[idx, "pitcher_id"] = pid
                out.at[idx, "pitcher_name"] = pname
                out.at[idx, "pitcher_resolution_method"] = method
            elif pname:
                exact = search_people_exact(s, pname)
                if exact:
                    out.at[idx, "pitcher_id"] = exact[0]["id"]
                    out.at[idx, "pitcher_name"] = pname
                    out.at[idx, "pitcher_resolution_method"] = "MLB_GAME_FEED_NAME_RESOLVED"
                else:
                    out.at[idx, "pitcher_name"] = pname
                    out.at[idx, "pitcher_resolution_method"] = method

    unresolved = out[out.pitcher_id.isna()].copy()
    for idx, row in unresolved.iterrows():
        js = live_feed_cache.get(int(row.game_pk), {})
        candidates = []
        for name in infer_name_candidates_from_text(public_text, row):
            exact = search_people_exact(s, name)
            for hit in exact:
                if str((hit.get("primaryPosition") or {}).get("code") if isinstance(hit.get("primaryPosition"), dict) else hit.get("primaryPosition")) in {"1", None, "P"}:
                    candidates.append({"id": hit.get("id"), "fullName": hit.get("fullName")})
        team_pitchers = candidate_team_pitchers_from_live_feed(js, row.team_id)
        by_id = {}
        for c in candidates + team_pitchers:
            if c.get("id") is not None:
                by_id[c["id"]] = c
        shortlisted = list(by_id.values())
        if len(shortlisted) == 1:
            out.at[idx, "pitcher_id"] = shortlisted[0]["id"]
            out.at[idx, "pitcher_name"] = shortlisted[0].get("fullName")
            out.at[idx, "pitcher_resolution_method"] = "PUBLIC_PLUS_LIVE_FEED_SINGLE_CANDIDATE"
        elif len(shortlisted) > 1:
            named = [c for c in shortlisted if norm(c.get("fullName")) == norm(row.pitcher_name)] if str(row.pitcher_name or "").strip() else []
            if len(named) == 1:
                out.at[idx, "pitcher_id"] = named[0]["id"]
                out.at[idx, "pitcher_name"] = named[0].get("fullName")
                out.at[idx, "pitcher_resolution_method"] = "SHORTLIST_NAME_MATCH"

    # Final integrity rule: if MLB schedule/game feed still does not supply a starter id, treat it as a deferred-data condition, not a fake success.
    for _, r in out[out.pitcher_id.isna()].iterrows():
        health.add("WARN", "STARTER_STILL_PENDING_FROM_UPSTREAM", r.game_pk, f"{r.team} starter not yet fully published by upstream sources.", "Environment rows remain valid; pitch-level model skipped for this side until upstream starter appears.")
    return out


def savant_query(s: requests.Session, pitcher_id: int, start: str, end_exclusive: str, raw: Path, health: Health, query_rows: list[dict[str, Any]], label: str) -> pd.DataFrame:
    q = {"type": "details", "player_type": "pitcher", "player_id": int(pitcher_id), "game_date_gt": start, "game_date_lt": end_exclusive, "min_pitches": 0, "min_results": 0}
    audit = {"pitcher_id": int(pitcher_id), "query_start": start, "query_end": end_exclusive, "window_label": label, "url": SAVANT, "request_params_json": json.dumps(q, sort_keys=True)}
    try:
        r = s.get(SAVANT, params=q, timeout=90)
        r.raise_for_status()
        p = raw / f"savant_{pitcher_id}_{label}.csv"
        p.write_bytes(r.content)
        d = pd.read_csv(p)
        d.columns = [norm(x).replace(" ", "_") for x in d.columns]
        idcol = first(d, "pitcher", "pitcher_id", "player_id")
        if not idcol:
            raise ValueError("No pitcher id column in Savant CSV")
        scoped = d[num(d[idcol]).eq(int(pitcher_id))].copy()
        audit.update({"http_status": r.status_code, "raw_file": str(p), "rows_returned": len(d), "id_column": idcol, "rows_scoped": len(scoped), "scope_pass": len(scoped) > 0})
        query_rows.append(audit)
        return scoped
    except Exception as e:
        audit.update({"error": f"{type(e).__name__}: {e}", "scope_pass": False})
        query_rows.append(audit)
        health.add("WARN", "SAVANT_FETCH_FAILED", pitcher_id, str(e), "Skip pitch-level model for this pitcher only.")
        return pd.DataFrame()


def summarize_window(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    pitch_col = first(df, "pitch_name", "pitch_type", "pitch")
    if not pitch_col:
        return pd.DataFrame()
    keep_num = [c for c in ["release_speed", "effective_speed", "release_spin_rate", "spin_axis", "active_spin", "active_spin_pct", "spin_efficiency", "pfx_x", "pfx_z", "release_extension"] if c in df.columns]
    for c in keep_num:
        df[c] = num(df[c])
    grp = df.groupby(pitch_col, dropna=False)
    out = grp.size().rename("pitches").to_frame().reset_index().rename(columns={pitch_col: "pitch_type"})
    for c in keep_num:
        out[c] = grp[c].mean().values
    total = out.pitches.sum()
    out["usage_pct"] = 100 * out.pitches / total if total else np.nan
    return out


def weighted_arsenal(season_df: pd.DataFrame, recent_df: pd.DataFrame, recent_weight: float = 0.65) -> pd.DataFrame:
    if season_df.empty and recent_df.empty:
        return pd.DataFrame()
    if season_df.empty:
        out = recent_df.copy(); out["window_basis"] = "RECENT_ONLY"; return out
    if recent_df.empty:
        out = season_df.copy(); out["window_basis"] = "SEASON_ONLY"; return out
    m = season_df.merge(recent_df, on="pitch_type", how="outer", suffixes=("_season", "_recent")).fillna(0)
    out = pd.DataFrame({"pitch_type": m.pitch_type})
    for metric in ["pitches", "release_speed", "effective_speed", "release_spin_rate", "spin_axis", "active_spin", "active_spin_pct", "spin_efficiency", "pfx_x", "pfx_z", "release_extension", "usage_pct"]:
        s_col = f"{metric}_season"
        r_col = f"{metric}_recent"
        if s_col not in m.columns and r_col not in m.columns:
            continue
        s = m[s_col] if s_col in m.columns else 0
        r = m[r_col] if r_col in m.columns else 0
        if metric == "pitches":
            out[metric] = s + r
        else:
            out[metric] = (1 - recent_weight) * s + recent_weight * r
    out["window_basis"] = "RECENT_65_SEASON_35"
    return out


def apply_environment_physics(arsenal: pd.DataFrame, branch: dict[str, Any]) -> pd.DataFrame:
    if arsenal.empty:
        return arsenal
    out = arsenal.copy()
    ratio = float(branch["density_ratio_to_ref"])
    wind_out = float(branch.get("wind_out_to_cf_kmh", 0) or 0)
    out["density_ratio_to_ref"] = ratio
    out["density_only_movement_change_pct"] = (ratio - 1) * 100
    if "pfx_x" in out.columns:
        out["projected_pfx_x"] = out["pfx_x"] * ratio
        out["delta_pfx_x_inches"] = (out["projected_pfx_x"] - out["pfx_x"]) * 12
    if "pfx_z" in out.columns:
        out["projected_pfx_z"] = out["pfx_z"] * ratio
        out["delta_pfx_z_inches"] = (out["projected_pfx_z"] - out["pfx_z"]) * 12
    if "release_speed" in out.columns:
        out["carry_wind_adjustment_ft_400ft"] = (wind_out / 10.0) * 3.0
        out["carry_density_adjustment_ft_400ft"] = (1 - ratio) * 5.2
        out["effective_plate_speed_delta_mph"] = (1 - ratio) * out["release_speed"] * 0.18
    active_col = "active_spin_pct" if "active_spin_pct" in out.columns else ("spin_efficiency" if "spin_efficiency" in out.columns else None)
    if active_col:
        out["active_spin_environment_index"] = out[active_col] * ratio
    out["model_label"] = "FIRST_ORDER_ENVIRONMENTAL_SENSITIVITY"
    return out


def autodetect_park_reference() -> Path:
    candidates = [
        Path(__file__).resolve().parent / "mlb_park_reference_full_corrected_v3.csv",
        Path.cwd() / "mlb_park_reference_full_corrected_v3.csv",
        Path(__file__).resolve().parent / "mlb_park_reference_verified.csv",
        Path.cwd() / "mlb_park_reference_verified.csv",
    ]
    hit = next((p for p in candidates if p.exists()), None)
    if not hit:
        raise FileNotFoundError("Park reference CSV not found beside the script or in the current working directory.")
    return hit


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_bundle(run_dir: Path) -> dict[str, Any]:
    csv_contracts = {
        "game_environment_audit.csv": None,
        "roof_evidence_audit.csv": None,
        "pitcher_query_audit.csv": None,
        "run_health_and_model_diagnostics.csv": None,
        "findings.csv": Health.COLUMNS,
    }

    def read_csv(name: str) -> pd.DataFrame:
        p = run_dir / name
        return safe_read_csv(p, csv_contracts.get(name))
    game_env = read_csv("game_environment_audit.csv")
    roof = read_csv("roof_evidence_audit.csv")
    q = read_csv("pitcher_query_audit.csv")
    diag = read_csv("run_health_and_model_diagnostics.csv")
    findings = read_csv("findings.csv")
    games = pd.DataFrame()
    if not game_env.empty:
        keep = [c for c in ["game_pk", "game_datetime_utc", "venue_name", "venue_name_api", "roofclass", "roof_state", "roof_decision_reason", "ballparkelevationm"] if c in game_env.columns]
        games = game_env[keep].drop_duplicates().sort_values(["game_datetime_utc", "game_pk"]) if keep else pd.DataFrame()
    summary = {
        "scheduled_games": int(games.game_pk.nunique()) if not games.empty and "game_pk" in games.columns else 0,
        "environment_rows": int(len(game_env)),
        "roof_evidence_rows": int(len(roof)),
        "pitcher_query_rows": int(len(q)),
        "findings": int(len(findings)),
        "conditional_retractable_games": int(games.loc[games.roof_state.eq("UNRESOLVED_RETRACTABLE_ONLY"), "game_pk"].nunique()) if not games.empty and "roof_state" in games.columns else 0,
    }
    return {
        "meta": {"version": VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "run_dir": str(run_dir.resolve())},
        "summary": summary,
        "games": json.loads(games.to_json(orient="records", date_format="iso")) if not games.empty else [],
        "game_environment_audit": json.loads(game_env.to_json(orient="records", date_format="iso")) if not game_env.empty else [],
        "roof_evidence_audit": json.loads(roof.to_json(orient="records")) if not roof.empty else [],
        "pitcher_query_audit": json.loads(q.to_json(orient="records")) if not q.empty else [],
        "run_health_and_model_diagnostics": json.loads(diag.to_json(orient="records")) if not diag.empty else [],
        "findings": json.loads(findings.to_json(orient="records")) if not findings.empty else [],
    }


def write_bundle_files(run_dir: Path) -> None:
    bundle = build_bundle(run_dir)
    (run_dir / "mlb_dashboard_data_bundle.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    health_json = {"export_type": "system_health", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "summary": bundle["summary"], "diagnostics": bundle["run_health_and_model_diagnostics"], "findings": bundle["findings"]}
    (run_dir / "mlb_dashboard_system_health.json").write_text(json.dumps(health_json, indent=2), encoding="utf-8")


def validate_environment_rows(game_env: pd.DataFrame, health: Health) -> None:
    if game_env.empty:
        health.add("FATAL", "NO_ENVIRONMENT_ROWS", "run", "No game environment rows were emitted.", "Stop the run.")
        return
    if ((game_env.roofclass == "OUTDOOR") & (game_env.roof_state != "NOT_APPLICABLE")).any():
        health.add("FATAL", "OUTDOOR_ROOF_STATE_VIOLATION", "run", "Outdoor venue carried illegal roof state.", "Fix roof branching.")
    if ((game_env.roofclass.str.contains("FIXED", na=False)) & (game_env.branch != "ENCLOSED")).any():
        health.add("FATAL", "FIXED_ROOF_BRANCH_VIOLATION", "run", "Fixed roof venue emitted non-enclosed branch.", "Fix branching.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--park-reference", default=None)
    ap.add_argument("--output-root", default="mlb_daily_outputs")
    ap.add_argument("--open-dashboard", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    health = Health()
    s = session()
    park_path = Path(args.park_reference) if args.park_reference else autodetect_park_reference()
    run_dir = ensure_dir(Path(args.output_root) / f"{args.date}_{utcstamp()}")
    raw = ensure_dir(run_dir / "raw")

    parks = load_parks(park_path)
    games = schedule(s, args.date, health)
    if games.empty:
        health.add("FATAL", "NO_SCHEDULED_GAMES", args.date, "No regular-season games returned for the requested date.", "Stop the run.")
        write_csv_contract(health.frame(), run_dir / "findings.csv", Health.COLUMNS)
        sys.exit(2)

    joined = join_parks(games, parks, health)
    joined = resolve_starters(s, joined, raw, health)

    roof_cache = {}
    roof_evidence_rows = []
    game_rows = []
    query_rows = []
    pitch_rows = []

    games_unique = joined.sort_values(["game_pk", "side"]).groupby("game_pk", as_index=False).first()
    branch_map: dict[tuple[int, str], dict[str, Any]] = {}

    for _, g in games_unique.iterrows():
        a = ambient(s, g, raw, health)
        if not a:
            continue
        roof_state, roof_reason, votes = resolve_roof_state(s, g, raw, health, roof_cache, roof_evidence_rows)
        branches = [outdoor_branch(g, a)] if roof_state in {"NOT_APPLICABLE", "OPEN"} else ([enclosed_branch(g, a)] if roof_state in {"FIXED_CLOSED", "CLOSED"} else [outdoor_branch(g, a), enclosed_branch(g, a)])
        for b in branches:
            rec = {
                "game_pk": g.game_pk,
                "game_datetime_utc": g.game_datetime_utc,
                "venue_name_api": g.venue_name_api,
                "venue_name": g.venuename,
                "roofclass": g.roofclass,
                "ballparkelevationm": g.ballparkelevationm,
                "roof_state": roof_state,
                "roof_decision_reason": roof_reason,
                "roof_votes_json": json.dumps(votes, sort_keys=True),
                "park_join_method": g.park_join_method,
                "home_plate_bearing_deg": g.homeplatebearingdeg,
                **a,
                **b,
            }
            game_rows.append(rec)
            branch_map[(int(g.game_pk), b["branch"])] = rec

    season_start = date(pd.to_datetime(args.date).year, 3, 1).isoformat()
    recent_start = (pd.to_datetime(args.date).date() - timedelta(days=30)).isoformat()
    end_exclusive = (pd.to_datetime(args.date).date() + timedelta(days=1)).isoformat()

    for _, r in joined.iterrows():
        if pd.isna(r.pitcher_id):
            continue
        season_df = savant_query(s, int(r.pitcher_id), season_start, end_exclusive, raw, health, query_rows, "season")
        recent_df = savant_query(s, int(r.pitcher_id), recent_start, end_exclusive, raw, health, query_rows, "recent30")
        arsenal = weighted_arsenal(summarize_window(season_df), summarize_window(recent_df))
        if arsenal.empty:
            continue
        for branch in ["OUTDOOR", "ENCLOSED"]:
            env = branch_map.get((int(r.game_pk), branch))
            if not env:
                continue
            phys = apply_environment_physics(arsenal, env)
            for c in ["game_pk", "side", "team", "pitcher_id", "pitcher_name", "pitcher_resolution_method"]:
                phys[c] = r[c]
            phys["venue_name"] = r.venuename
            phys["branch"] = branch
            phys["roof_state"] = env["roof_state"]
            phys["air_density_kg_m3"] = env["air_density_kg_m3"]
            pitch_rows.extend(phys.to_dict(orient="records"))

    game_env = pd.DataFrame(game_rows)
    validate_environment_rows(game_env, health)
    game_env.to_csv(run_dir / "game_environment_audit.csv", index=False)
    pd.DataFrame(roof_evidence_rows).to_csv(run_dir / "roof_evidence_audit.csv", index=False)
    pd.DataFrame(query_rows).to_csv(run_dir / "pitcher_query_audit.csv", index=False)
    pd.DataFrame(pitch_rows).to_csv(run_dir / "pitch_physics_environment.csv", index=False)

    starters_resolved = joined.pitcher_id.notna().sum()
    diag_rows = [
        {"check": "scheduled_games", "value": float(games_unique.game_pk.nunique()), "status": "PASS"},
        {"check": "scheduled_starters_resolved", "value": float(starters_resolved), "status": "PASS" if starters_resolved == len(joined) else "WARN_UPSTREAM_PENDING"},
        {"check": "query_audit_rows", "value": float(len(query_rows)), "status": "PASS" if len(query_rows) > 0 else "WARN_NO_PITCH_ROWS"},
        {"check": "conditional_retractable_games", "value": float(game_env.loc[game_env.roof_state.eq("UNRESOLVED_RETRACTABLE_ONLY"), "game_pk"].nunique()) if not game_env.empty else 0.0, "status": "INFO_MODELED_CONDITIONAL"},
        {"check": "air_density_range_kg_m3_across_todays_venues", "value": float(game_env.air_density_kg_m3.max() - game_env.air_density_kg_m3.min()) if not game_env.empty else float('nan'), "status": "INFO"},
    ]
    pd.DataFrame(diag_rows).to_csv(run_dir / "run_health_and_model_diagnostics.csv", index=False)
    write_csv_contract(health.frame(), run_dir / "findings.csv", Health.COLUMNS)

    manifest = {"version": VERSION, "requested_date": args.date, "run_dir": str(run_dir.resolve()), "fatal": health.fatal(), "files": {p.name: sha(p) for p in run_dir.iterdir() if p.is_file()}}
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_bundle_files(run_dir)

    print(json.dumps({
        "status": "FAIL" if health.fatal() else "COMPLETE",
        "version": VERSION,
        "requested_date": args.date,
        "run_dir": str(run_dir.resolve()),
        "games": int(games_unique.game_pk.nunique()),
        "starters_resolved": int(starters_resolved),
        "environment_rows": int(len(game_env)),
        "pitch_rows": int(len(pitch_rows)),
        "findings": int(len(health.rows)),
    }, indent=2))

    if args.open_dashboard:
        dashboard = run_dir / "mlb-pitch-environment-live-dashboard.html"
        if dashboard.exists():
            webbrowser.open(dashboard.resolve().as_uri())


if __name__ == "__main__":
    main()
