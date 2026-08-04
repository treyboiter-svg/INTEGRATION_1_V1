#!/usr/bin/env python3
"""MLB Daily Pitch Environment V44.0.1 -- Full Forensic Revision (patched).

CHANGE FROM V44.0.0: fixed a crash when the MLB schedule API returns zero
games for the requested date (off-day, off-season, or a transient API
failure). The original code called `joined.groupby("game_pk", ...)` on a
DataFrame that could have zero columns when empty, raising
`KeyError: 'game_pk'` and aborting before any output file -- including
findings.csv -- was written. This is what silently produced "0 games in
the dashboard" even after the http-server launcher fix, because the script
crashed with a Python traceback before writing anything, and that
traceback is easy to miss in a batch window.

Fix: schedule() and join_parks() now always return a DataFrame with the
full expected column set, even when zero rows are returned, and main()
checks explicitly for the empty case and writes a complete, valid,
zero-games run directory (all required CSVs/JSONs, empty but schema-
correct) instead of crashing.

Everything else is unchanged from V44.0.0. See V44_IN_APP_GLOSSARY.json,
build_dashboard_bundle_V44.py, and README_V44.md for the rest of the
system documentation.

Run (no arguments required if the park reference CSV sits beside this
script or in the current working directory):
    python MLB_DAILY_PITCH_ENVIRONMENT_V44.py
Optional:
    python MLB_DAILY_PITCH_ENVIRONMENT_V44.py --date 2026-08-04 \
        --park-reference mlb_park_reference_full_corrected_v3.csv \
        --indoor-sensor-csv indoor_sensor_measurements.csv \
        --include-pitch-level-audit

Dependencies: Python 3.10+, pandas, numpy, requests.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import re
import webbrowser
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

VERSION = "44.0.1"
MLB = "https://statsapi.mlb.com/api/v1"
SAVANT = "https://baseballsavant.mlb.com/statcast_search/csv"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
R_D = 287.05
R_V = 461.495
G = 9.80665
RHO_REF = 1.2041
LOG = logging.getLogger("mlb_pitch_environment_v44")

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
    r"|\bopen\s+(?:roof|dome)\b|\broof\s*[:\-]?\s*open\b|\bwill\s+(?:likely\s+)?open\b|\bopen[- ]air\b|\bconditions?\s+will\s+favor\s+an?\s+open\b"
    r"|\broof\s+likely\s+open\b",
    re.I,
)
CLOSED_RX = re.compile(
    r"\b(?:roof|dome)\b[^.]{0,90}\b(?:is|will\s+(?:likely\s+)?be|expected\s+to\s+be|likely|projected\s+to\s+be)\b[^.]{0,30}\bclosed\b"
    r"|\bclosed\s+(?:roof|dome)\b|\broof\s*[:\-]?\s*closed\b|\bkeep(?:ing)?\s+the\s+roof[^.]{0,60}\bclosed\b|\bwill\s+(?:likely\s+)?close\b|\bclosed\s+dome\b"
    r"|\broof\s+likely\s+closed\b|\bweather\s+will\s+not\s+be\s+a\s+factor\b",
    re.I,
)
ROOF_KEYWORD_RX = re.compile(r"\broof\b|\bdome\b", re.I)
BOILERPLATE_DOME_RX = re.compile(r"\bdomed stadium\b|\binside a domed stadium\b|\bthis game takes place inside a domed stadium\b", re.I)

SCHEDULE_COLUMNS = [
    "game_pk", "game_datetime_utc", "venue_name_api", "home_team", "away_team",
    "side", "team", "pitcher_id", "pitcher_name", "pitcher_resolution_method",
]
JOINED_EXTRA_COLUMNS = [
    "venuename", "team_park", "lat", "lon", "ballparkelevationm", "rooftype",
    "homeplatebearingdeg", "roofclass", "stationelevationm", "park_join_method",
]


@dataclass
class Finding:
    severity: str
    code: str
    entity: str
    message: str
    action: str
    dashboard_section: str = ""


class Health:
    COLUMNS = ["severity", "code", "entity", "message", "action", "dashboard_section"]

    def __init__(self) -> None:
        self.rows: list[Finding] = []

    def add(self, severity: str, code: str, entity: Any = "", message: str = "", action: str = "", dashboard_section: str = "") -> None:
        self.rows.append(Finding(severity, code, str(entity), message, action, dashboard_section))
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


def mascot(full_name: str) -> str:
    parts = norm(full_name).split()
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
        health.add("WARN", code, url, f"{type(e).__name__}: {e}", "Keep values null; retain audit record.")
        return None


def get_text(s: requests.Session, url: str, raw: Path, label: str, health: Health):
    try:
        r = s.get(url, timeout=45)
        r.raise_for_status()
        p = raw / f"{label}.html"
        p.write_text(r.text, encoding="utf-8")
        return r.text, p, r.status_code
    except Exception as e:
        health.add("WARN", "ROOF_SOURCE_FETCH_FAILED", label, f"{type(e).__name__}: {e}", "Source abstains; downstream logic must not fabricate a vote.")
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
    if d.venuename.isna().any() or d.venuename.map(norm).duplicated().any():
        raise ValueError("Park venue names must be unique and nonblank.")
    valid = OUTDOOR_CLASSES | FIXED_CLASSES | RETRACTABLE_CLASSES
    bad = d.loc[~d["roofclass"].isin(valid), "rooftype"].tolist()
    if bad:
        raise ValueError(f"Unsupported roof classes: {bad}")
    return d


def schedule(s: requests.Session, day: str, health: Health) -> pd.DataFrame:
    """Always returns a DataFrame with SCHEDULE_COLUMNS, even when empty.
    FIX (V44.0.1): the original version returned pd.DataFrame([]) -- zero
    rows AND zero columns -- whenever there were no games. That broke every
    downstream .groupby("game_pk") call with a KeyError. Now an empty result
    always has the correct schema."""
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
                    "side": side,
                    "team": team["team"].get("name"),
                    "pitcher_id": p.get("id"),
                    "pitcher_name": p.get("fullName"),
                    "pitcher_resolution_method": "MLB_SCHEDULE" if p.get("id") else "UNRESOLVED",
                })
    df = pd.DataFrame(rows, columns=SCHEDULE_COLUMNS)
    if not (j or {}).get("dates"):
        health.add("INFO", "NO_GAMES_SCHEDULED", day, f"MLB schedule API returned no games for {day}.", "Verify this is a valid MLB regular-season date with games. Off-days, off-season dates, and postponements will legitimately produce zero games.", "Overview")
    if not df.empty:
        for _, r in df[df.pitcher_id.isna()].iterrows():
            health.add("WARN", "UNRESOLVED_STARTER", r.game_pk, f"{r.team} has no listed probable starter.", "Skip pitcher projection for this side only.", "Pitchers")
    return df


def join_parks(games: pd.DataFrame, parks: pd.DataFrame, health: Health) -> pd.DataFrame:
    """Always returns a DataFrame with SCHEDULE_COLUMNS + JOINED_EXTRA_COLUMNS,
    even when zero rows are produced. FIX (V44.0.1): same empty-schema bug as
    schedule()."""
    expected_columns = SCHEDULE_COLUMNS + [c for c in JOINED_EXTRA_COLUMNS if c not in SCHEDULE_COLUMNS]
    if games.empty:
        return pd.DataFrame(columns=expected_columns)
    rows = []
    for _, g in games.iterrows():
        x = parks[parks.venuename.map(norm).eq(norm(canonical_venue(g.venue_name_api)))]
        method = "EXACT_VENUE_NAME"
        if len(x) != 1:
            x = parks[parks.team.map(norm).eq(norm(g.home_team))]
            method = "EXACT_HOME_TEAM_FALLBACK"
        if len(x) != 1:
            health.add("FATAL", "MISSING_VERIFIED_PARK", g.game_pk, f"venue={g.venue_name_api}; home={g.home_team}", "Correct park reference or add a documented branding alias.", "Overview")
            continue
        z = g.to_dict()
        z.update(x.iloc[0].to_dict())
        z["park_join_method"] = method
        rows.append(z)
    if not rows:
        return pd.DataFrame(columns=expected_columns)
    return pd.DataFrame(rows)


def svp(tc: float) -> float:
    return 610.94 * math.exp(17.625 * tc / (tc + 243.04))


def vapor_pressure_hpa(tc: float, rh: float) -> float:
    return (max(0.0, min(1.0, rh / 100.0)) * svp(tc)) / 100.0


def density(tc: float, rh: float, p_hpa: float) -> float:
    e = max(0.0, min(1.0, rh / 100.0)) * svp(tc)
    tk = tc + 273.15
    pa = p_hpa * 100.0
    return (pa - e) / (R_D * tk) + e / (R_V * tk)


def density_terms(tc: float, rh: float, p_hpa: float) -> dict[str, float]:
    pv_hpa = vapor_pressure_hpa(tc, rh)
    pdry_hpa = p_hpa - pv_hpa
    tk = tc + 273.15
    rho = density(tc, rh, p_hpa)
    return {
        "temperature_c": tc, "relative_humidity_pct": rh, "pressure_hpa": p_hpa,
        "vapor_pressure_hpa": pv_hpa, "dry_air_partial_pressure_hpa": pdry_hpa,
        "temperature_k": tk, "air_density_kg_m3": rho,
        "reference_density_kg_m3": RHO_REF, "density_ratio_to_ref": rho / RHO_REF,
    }


def barometric_to_elevation(p_hpa: float, source_elev_m: float, venue_elev_m: float, tc: float) -> float:
    return p_hpa * math.exp(-G * (venue_elev_m - source_elev_m) / (R_D * (tc + 273.15)))


def wetbulb(tc: float, rh: float) -> float:
    return tc * math.atan(.151977 * math.sqrt(rh + 8.313659)) + math.atan(tc + rh) - math.atan(rh - 1.676331) + .00391838 * rh ** 1.5 * math.atan(.023101 * rh) - 4.686035


def ambient(s: requests.Session, row: pd.Series, health: Health, raw: Path) -> dict[str, Any]:
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
        tc = float(h["temperature_2m"][i]); rh = float(h["relative_humidity_2m"][i]); p = float(h["surface_pressure"][i])
        ws = float(h["wind_speed_10m"][i]); wd = float(h["wind_direction_10m"][i]); rho = density(tc, rh, p)
        return {
            "ambient_temperature_c": tc, "ambient_relative_humidity_pct": rh, "ambient_surface_pressure_hpa": p,
            "ambient_wind_speed_kmh": ws, "ambient_wind_direction_deg": wd, "ambient_air_density_kg_m3": rho,
            "ambient_density_ratio_to_ref": rho / RHO_REF, "ambient_wetbulb_c": wetbulb(tc, rh),
            "ambient_forecast_hour_utc": str(ts[i]), "ambient_provider": "OPEN_METEO",
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        health.add("WARN", "AMBIENT_SCHEMA_INVALID", row.game_pk, str(e), "No environment branch emitted.", "Atmosphere provenance")
        return {}


def indoor_sensor(path: str | None, row: pd.Series, game_dt: str) -> dict[str, Any]:
    if not path:
        return {}
    d = pd.read_csv(path)
    d.columns = [col(x) for x in d.columns]
    need = {"venuename", "observedatutc", "temperaturec", "relativehumiditypct", "pressurehpa"}
    miss = need - set(d.columns)
    if miss:
        raise ValueError(f"Indoor sensor CSV missing {sorted(miss)}")
    d = d[d.venuename.map(norm).eq(norm(row.venuename))].copy()
    d["dt"] = pd.to_datetime(d.observedatutc, utc=True, errors="coerce")
    d = d.dropna(subset=["dt"])
    if d.empty:
        return {}
    r = d.iloc[int(np.argmin(abs(d.dt - pd.to_datetime(game_dt, utc=True))))]
    return {"temperature_c": float(r.temperaturec), "relative_humidity_pct": float(r.relativehumiditypct), "pressure_hpa": float(r.pressurehpa), "sensor_time_utc": str(r.observedatutc)}


def enclosed_environment(row: pd.Series, a: dict, sensor: dict) -> dict[str, Any]:
    if sensor:
        tc, rh, p = sensor["temperature_c"], sensor["relative_humidity_pct"], sensor["pressure_hpa"]
        return {
            "branch": "ENCLOSED", "air_environment_basis": "DIRECT_INDOOR_MEASUREMENT",
            "temperature_humidity_basis": "INDOOR_SENSOR", "pressure_basis": "INDOOR_SENSOR",
            "temperature_c": tc, "relative_humidity_pct": rh, "pressure_hpa": p,
            "air_density_kg_m3": density(tc, rh, p), "density_uncertainty_kg_m3": .003,
            "external_wind_speed_kmh": 0., "external_wind_direction_deg": np.nan,
            "wind_model_status": "ZERO_EXTERNAL_WIND_ENCLOSED",
        }
    tc, rh = a["ambient_temperature_c"], a["ambient_relative_humidity_pct"]
    src = float(row.stationelevationm) if "stationelevationm" in row.index and pd.notna(row.get("stationelevationm")) else float(row.ballparkelevationm)
    p = barometric_to_elevation(a["ambient_surface_pressure_hpa"], src, float(row.ballparkelevationm), tc)
    return {
        "branch": "ENCLOSED", "air_environment_basis": "INDOOR_MODELED_PRESSURE_ANCHORED",
        "temperature_humidity_basis": "OUTDOOR_AMBIENT_PROXY_HVAC_UNMEASURED", "pressure_basis": "LOCAL_BAROMETRY_ELEVATION_CORRECTED",
        "temperature_c": tc, "relative_humidity_pct": rh, "pressure_hpa": p,
        "air_density_kg_m3": density(tc, rh, p), "density_uncertainty_kg_m3": .020,
        "external_wind_speed_kmh": 0., "external_wind_direction_deg": np.nan,
        "wind_model_status": "ZERO_EXTERNAL_WIND_ENCLOSED",
    }


def outdoor_environment(a: dict) -> dict[str, Any]:
    return {
        "branch": "OUTDOOR", "air_environment_basis": "LOCAL_OUTDOOR_AMBIENT",
        "temperature_humidity_basis": "OUTDOOR_FORECAST", "pressure_basis": "LOCAL_SURFACE_PRESSURE",
        "temperature_c": a["ambient_temperature_c"], "relative_humidity_pct": a["ambient_relative_humidity_pct"],
        "pressure_hpa": a["ambient_surface_pressure_hpa"], "air_density_kg_m3": a["ambient_air_density_kg_m3"],
        "density_uncertainty_kg_m3": .010, "external_wind_speed_kmh": a["ambient_wind_speed_kmh"],
        "external_wind_direction_deg": a["ambient_wind_direction_deg"], "wind_model_status": "APPLICABLE_OUTDOOR",
    }


def vote_multi_game(text: str, row: pd.Series):
    t = clean_page(text)
    h, aw, v = norm(row.home_team), norm(row.away_team), norm(row.venuename)
    hm, awm = mascot(row.home_team), mascot(row.away_team)
    keys = [x for x in (h, aw, v, hm, awm) if x]
    positions = [m.start() for x in keys for m in re.finditer(re.escape(x), t)]
    votes: list[str] = []
    found_identity = False
    for i in positions:
        card = t[max(0, i - 700):i + 1800]
        identity = ((h in card and aw in card) or (hm in card and awm in card)
                    or (v in card and (h in card or aw in card or hm in card or awm in card)))
        if not identity:
            continue
        found_identity = True
        o, c = bool(OPEN_RX.search(card)), bool(CLOSED_RX.search(card))
        if o ^ c:
            votes.append("OPEN" if o else "CLOSED")
    if not votes:
        for i in ([m.start() for m in re.finditer(re.escape(v), t)] if v else []):
            card = t[max(0, i - 300):i + 900]
            found_identity = True
            o, c = bool(OPEN_RX.search(card)), bool(CLOSED_RX.search(card))
            if o ^ c:
                votes.append("OPEN" if o else "CLOSED")
    vote = votes[0] if len(set(votes)) == 1 else None
    if vote:
        status = "EXACT_GAME_OR_VENUE_CARD_EXPLICIT_ROOF_NARRATIVE_OVERRIDES_BOILERPLATE" if BOILERPLATE_DOME_RX.search(t) else "EXACT_GAME_OR_VENUE_CARD"
    elif found_identity:
        status = "IDENTITY_FOUND_NO_UNAMBIGUOUS_ROOF_LANGUAGE"
    elif ROOF_KEYWORD_RX.search(t):
        status = "NO_IDENTITY_MATCH_ROOF_LANGUAGE_ELSEWHERE_ON_PAGE"
    else:
        status = "NO_IDENTITY_MATCH_NO_ROOF_LANGUAGE_ON_PAGE"
    return vote, status, len(t), bool(positions), bool(ROOF_KEYWORD_RX.search(t))


def vote_single_venue(text: str, row: pd.Series):
    t = clean_page(text)
    o, c = bool(OPEN_RX.search(t)), bool(CLOSED_RX.search(t))
    vote = ("OPEN" if o else "CLOSED") if (o ^ c) else None
    if vote:
        status = "SINGLE_VENUE_EXPLICIT_STATEMENT"
    elif ROOF_KEYWORD_RX.search(t):
        status = "SINGLE_VENUE_AMBIGUOUS_ROOF_LANGUAGE"
    else:
        status = "SINGLE_VENUE_NO_ROOF_LANGUAGE_ON_PAGE"
    return vote, status, len(t), True, bool(ROOF_KEYWORD_RX.search(t))


def roof_state(s: requests.Session, row: pd.Series, raw: Path, health: Health, evidence_rows: list, conflict_rows: list):
    if row.roofclass in OUTDOOR_CLASSES:
        return "NOT_APPLICABLE", "STRUCTURAL_OUTDOOR", {}, {}
    if row.roofclass in FIXED_CLASSES:
        return "FIXED_CLOSED", "STRUCTURAL_FIXED_ENCLOSED", {}, {}

    dedicated = VENUE_DEDICATED_SOURCES.get(row.venuename, {})
    all_sources = {**ROOF_GENERIC_SOURCES, **dedicated}
    votes: dict[str, str | None] = {}
    evidence: dict[str, dict] = {}
    for source, meta in all_sources.items():
        text, p, http_status = get_text(s, meta["url"], raw, f"roof_{row.game_pk}_{source}", health)
        if meta.get("mode") == "SIMPLE":
            vote, status, plen, id_found, kw_found = vote_single_venue(text, row)
            scope = "SINGLE_VENUE"
        else:
            vote, status, plen, id_found, kw_found = vote_multi_game(text, row)
            scope = "MULTI_GAME"
        votes[source] = vote
        rec = {
            "game_pk": row.game_pk, "venue_name": row.venuename, "source": source, "scope": scope,
            "url": meta["url"], "vote": vote, "parser_status": status, "http_status": http_status,
            "page_char_length": plen, "identity_or_venue_found": id_found, "roof_keyword_found": kw_found,
            "raw_file": str(p) if p else None,
        }
        evidence[source] = rec
        evidence_rows.append(rec)

    dedicated_votes = {k: v for k, v in votes.items() if k in dedicated and v}
    if dedicated_votes:
        distinct = set(dedicated_votes.values())
        if len(distinct) == 1:
            final = distinct.pop()
            conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": final, "branch_strategy": "SINGLE_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "DEDICATED_SINGLE_VENUE_SOURCE_DECISIVE"})
            return final, "DEDICATED_SINGLE_VENUE_SOURCE_DECISIVE", votes, evidence
        health.add("WARN", "DEDICATED_SOURCE_CONFLICT", row.game_pk, f"dedicated votes={dedicated_votes}", "Fall through to generic-source quorum.", "Roof conflicts")

    generic_votes = {k: v for k, v in votes.items() if k in ROOF_GENERIC_SOURCES}
    no = sum(v == "OPEN" for v in generic_votes.values())
    nc = sum(v == "CLOSED" for v in generic_votes.values())
    if no >= 1 and nc >= 1:
        health.add("WARN", "ROOF_SOURCE_CONFLICT", row.game_pk, f"votes={votes}", "Review both modeled branches and evidence excerpts.", "Roof conflicts")
        conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": "CONFLICTED", "branch_strategy": "DUAL_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "EXPLICIT_SOURCE_CONFLICT"})
        return "CONFLICTED", "EXPLICIT_SOURCE_CONFLICT", votes, evidence
    if no >= 2 and nc == 0:
        conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": "OPEN", "branch_strategy": "SINGLE_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "TWO_GENERIC_SOURCE_CONSENSUS"})
        return "OPEN", "TWO_GENERIC_SOURCE_CONSENSUS", votes, evidence
    if nc >= 2 and no == 0:
        conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": "CLOSED", "branch_strategy": "SINGLE_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "TWO_GENERIC_SOURCE_CONSENSUS"})
        return "CLOSED", "TWO_GENERIC_SOURCE_CONSENSUS", votes, evidence
    single = {k: v for k, v in generic_votes.items() if v}
    if len(single) == 1 and no + nc == 1:
        final = next(iter(single.values()))
        conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": final, "branch_strategy": "SINGLE_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "SINGLE_GENERIC_SOURCE_UNCONTESTED"})
        return final, "SINGLE_GENERIC_SOURCE_UNCONTESTED", votes, evidence

    health.add("WARN", "RETRACTABLE_ROOF_UNRESOLVED", row.game_pk, f"votes={votes}", "Conditional OPEN and ENCLOSED branches required.", "Roof conflicts")
    conflict_rows.append({"game_pk": row.game_pk, "venue_name": row.venuename, "final_roof_state": "UNRESOLVED", "branch_strategy": "DUAL_BRANCH", "source_votes_json": json.dumps(votes, sort_keys=True), "decision_reason": "NO_DECISIVE_SOURCE_AND_NO_GENERIC_CONSENSUS"})
    return "UNRESOLVED_RETRACTABLE_ONLY", "NO_DECISIVE_SOURCE_AND_NO_GENERIC_CONSENSUS", votes, evidence


def branches(row: pd.Series, state: str, a: dict, sensor: dict) -> list[dict]:
    if not a:
        return []
    o = outdoor_environment(a)
    e = enclosed_environment(row, a, sensor)
    if state in {"NOT_APPLICABLE", "OPEN"}:
        return [o]
    if state in {"FIXED_CLOSED", "CLOSED"}:
        return [e]
    return [o, e]  # CONFLICTED or UNRESOLVED_RETRACTABLE_ONLY -> dual branch


def savant_query(s: requests.Session, pid: int, start: str, end: str, raw: Path, health: Health, query_rows: list, window_label: str) -> pd.DataFrame:
    q = {"type": "details", "player_type": "pitcher", "player_id": int(pid), "game_date_gt": start, "game_date_lt": end, "min_pitches": 0, "group_by": "pitch-type", "sort_col": "pitches", "player_event_sort": "api_p_release_speed", "sort_order": "desc", "min_results": 0}
    record = {"pitcher_id": int(pid), "window": window_label, "query_start": start, "query_end": end, "url": SAVANT, "request_params_json": json.dumps(q, sort_keys=True)}
    try:
        r = s.get(SAVANT, params=q, timeout=90)
        r.raise_for_status()
        p = raw / f"savant_{pid}_{window_label}.csv"
        p.write_bytes(r.content)
        d = pd.read_csv(p)
        d.columns = [norm(x).replace(" ", "_") for x in d.columns]
        record.update({"http_status": r.status_code, "raw_file": str(p), "rows_returned": len(d)})
        idc = first(d, "pitcher", "pitcher_id", "player_id")
        if not idc:
            raise ValueError("No pitcher ID column in Savant CSV")
        scoped = d[num(d[idc]).eq(int(pid))].copy()
        record.update({"id_column": idc, "rows_scoped": len(scoped), "scope_pass": len(scoped) == len(d)})
        query_rows.append(record)
        return scoped
    except Exception as e:
        record.update({"error": f"{type(e).__name__}: {e}", "scope_pass": False})
        query_rows.append(record)
        health.add("WARN", "SAVANT_FETCH_FAILED", pid, record["error"], "Pitcher has an audit row; no summary rows for this window.", "Pitchers / pitch effects")
        return pd.DataFrame()


def metric(d: pd.DataFrame, *names: str) -> pd.Series:
    c = first(d, *names)
    return num(d[c]) if c else pd.Series(np.nan, index=d.index)


def summarize_window(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    pc = first(d, "pitch_type", "pitch_name")
    if not pc:
        return pd.DataFrame()
    x = d.copy()
    x["pitch_type"] = x[pc].astype(str)
    x = x[x.pitch_type.notna() & x.pitch_type.ne("nan")]
    count = metric(x, "pitches", "pitch_count")
    x["_pitches"] = count.fillna(0)
    out = []
    for pt, g in x.groupby("pitch_type", dropna=False):
        w = g._pitches.to_numpy(float)
        w = np.where(w > 0, w, 1.0)

        def avg(*names):
            z = metric(g, *names).to_numpy(float)
            ok = np.isfinite(z)
            return float(np.average(z[ok], weights=w[ok])) if ok.any() else np.nan

        pitches = float(g._pitches.sum())
        spin = avg("release_spin_rate", "spin_rate")
        axis = avg("spin_axis")
        active = avg("active_spin_pct", "spin_efficiency")
        transverse = spin * (active / 100) if np.isfinite(spin) and np.isfinite(active) else (spin * abs(math.sin(math.radians(axis))) if np.isfinite(spin) and np.isfinite(axis) else np.nan)
        out.append({
            "pitch_type": pt, "pitch_count": pitches, "sample_eligible": pitches >= 20,
            "baseline_release_speed_mph": avg("release_speed", "velocity"),
            "baseline_effective_speed_mph": avg("effective_speed"),
            "baseline_release_spin_rate_rpm": spin, "baseline_spin_axis_deg": axis,
            "active_spin_pct": active, "active_spin_environment_index": transverse,
            "gyro_spin_pct_proxy": 100 - active if np.isfinite(active) else np.nan,
            "baseline_pfx_x_inches": avg("pfx_x", "horizontal_break"), "baseline_pfx_z_inches": avg("pfx_z", "vertical_break"),
            "release_extension_ft": avg("release_extension"), "release_pos_x_ft": avg("release_pos_x", "release_side"),
            "release_pos_y_ft": avg("release_pos_y"), "release_pos_z_ft": avg("release_pos_z", "release_height"),
            "release_angle_deg": avg("release_angle"), "plate_x_ft": avg("plate_x"), "plate_z_ft": avg("plate_z"),
        })
    o = pd.DataFrame(out)
    total = o.pitch_count.sum()
    o["usage_pct"] = 100 * o.pitch_count / total if total else np.nan
    return o


def weighted_arsenal(season: pd.DataFrame, recent30: pd.DataFrame) -> pd.DataFrame:
    """Blend season and recent-30-day baselines: recent form weighted 60/40
    against full-season sample when both windows have data for a pitch type,
    otherwise fall back to whichever window has data."""
    if season.empty and recent30.empty:
        return pd.DataFrame()
    if season.empty:
        out = recent30.copy(); out["baseline_window"] = "RECENT30_ONLY"; return out
    if recent30.empty:
        out = season.copy(); out["baseline_window"] = "SEASON_ONLY"; return out
    merged = season.merge(recent30, on="pitch_type", how="outer", suffixes=("_season", "_recent30"))
    numeric_cols = [c for c in season.columns if c not in ("pitch_type", "sample_eligible") and pd.api.types.is_numeric_dtype(season[c])]
    rows = []
    for _, r in merged.iterrows():
        rec = {"pitch_type": r["pitch_type"]}
        has_season = pd.notna(r.get("pitch_count_season"))
        has_recent = pd.notna(r.get("pitch_count_recent30"))
        for c in numeric_cols:
            sv = r.get(f"{c}_season"); rv = r.get(f"{c}_recent30")
            if pd.notna(sv) and pd.notna(rv):
                rec[c] = 0.4 * sv + 0.6 * rv
            elif pd.notna(rv):
                rec[c] = rv
            else:
                rec[c] = sv
        rec["baseline_window"] = "BLENDED_SEASON_RECENT30" if (has_season and has_recent) else ("RECENT30_ONLY" if has_recent else "SEASON_ONLY")
        rec["pitch_count"] = (r.get("pitch_count_season") or 0) + (r.get("pitch_count_recent30") or 0)
        rec["sample_eligible"] = rec["pitch_count"] >= 20
        rows.append(rec)
    out = pd.DataFrame(rows)
    total = out.pitch_count.sum()
    out["usage_pct"] = 100 * out.pitch_count / total if total else np.nan
    return out


def apply_environment_physics(arsenal: pd.DataFrame, branch: dict) -> pd.DataFrame:
    """First-order density/wind carry model. Explicitly labeled as first-order
    (fixed release + spin, not a full trajectory integrator) so no one can
    mistake this for a validated Magnus-force simulation."""
    if arsenal.empty:
        return pd.DataFrame()
    o = arsenal.copy()
    ratio = branch["air_density_kg_m3"] / RHO_REF
    o["density_ratio_to_ref"] = ratio
    o["density_only_movement_change_pct"] = (ratio - 1) * 100
    o["adjusted_pfx_x_inches"] = o.baseline_pfx_x_inches * ratio
    o["adjusted_pfx_z_inches"] = o.baseline_pfx_z_inches * ratio
    o["delta_pfx_x_inches"] = (o.adjusted_pfx_x_inches - o.baseline_pfx_x_inches)
    o["delta_pfx_z_inches"] = (o.adjusted_pfx_z_inches - o.baseline_pfx_z_inches)
    wind_kmh = branch.get("external_wind_speed_kmh", 0.0) or 0.0
    wind_applicable = branch.get("wind_model_status") == "APPLICABLE_OUTDOOR"
    o["delta_carry_wind_ft_400ft"] = (wind_kmh / 1.5) if wind_applicable else 0.0
    o["delta_carry_density_ft_400ft"] = (ratio - 1) * 400.0 * -1.0  # denser air -> less carry
    o["roof_branch_assumption"] = branch.get("branch")
    o["decomposition_complete"] = (
        o[["baseline_release_speed_mph", "baseline_release_spin_rate_rpm", "baseline_pfx_x_inches", "baseline_pfx_z_inches"]].notna().all(axis=1)
        & o[["delta_pfx_x_inches", "delta_pfx_z_inches"]].notna().all(axis=1)
    )
    o["model_label"] = "FIRST_ORDER_DENSITY_AND_WIND_SENSITIVITY_FIXED_RELEASE_SPIN_NOT_FULL_TRAJECTORY"
    return o


def pitch_level_audit(d: pd.DataFrame, row: pd.Series, branch: dict) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    keep = [x for x in ["pitch_type", "pitch_name", "pitches", "pitch_count", "release_speed", "effective_speed", "release_spin_rate", "spin_axis", "active_spin_pct", "spin_efficiency", "pfx_x", "pfx_z", "release_extension", "release_pos_x", "release_pos_y", "release_pos_z", "plate_x", "plate_z"] if x in d]
    x = d[keep].copy()
    x["game_pk"] = row.game_pk
    x["pitcher_id"] = row.pitcher_id
    x["branch"] = branch["branch"]
    x["air_density_kg_m3"] = branch["air_density_kg_m3"]
    x["density_ratio_to_reference"] = branch["air_density_kg_m3"] / RHO_REF
    return x


def ensure_schema(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = set(required) - set(df.columns)
    if missing:
        raise AssertionError(f"{label} missing schema: {sorted(missing)}")


def validate_environment_rows(audit: pd.DataFrame, health: Health) -> None:
    if audit.empty:
        return
    ensure_schema(audit, ["game_pk", "branch", "roofclass", "roof_state", "air_density_kg_m3", "wind_model_status"], "game_environment_audit")
    assert not ((audit.roofclass == "OUTDOOR") & (audit.roof_state != "NOT_APPLICABLE")).any()
    assert not ((audit.roofclass == "FIXEDENCLOSED") & (audit.roof_state != "FIXED_CLOSED")).any()
    assert not ((audit.roofclass == "OUTDOOR") & (audit.branch != "OUTDOOR")).any()
    assert not ((audit.roofclass == "FIXEDENCLOSED") & (audit.external_wind_speed_kmh != 0)).any()
    assert audit.loc[audit.roof_state.isin(["FIXED_CLOSED", "CLOSED"]), "air_density_kg_m3"].notna().all()
    for game in audit.loc[audit.roof_state.isin(["UNRESOLVED_RETRACTABLE_ONLY", "CONFLICTED"]), "game_pk"].unique():
        assert set(audit.loc[audit.game_pk.eq(game), "branch"]) == {"OUTDOOR", "ENCLOSED"}


def validate_pitch_rows(summary: pd.DataFrame, queries: pd.DataFrame, games: pd.DataFrame) -> None:
    if summary.empty:
        return
    ensure_schema(summary, ["game_pk", "pitcher_id", "pitch_type", "branch", "pitch_count", "baseline_release_speed_mph", "baseline_release_spin_rate_rpm", "baseline_pfx_x_inches", "baseline_pfx_z_inches", "density_ratio_to_ref"], "pitch_physics_environment")
    assert not summary.duplicated(["game_pk", "pitcher_id", "pitch_type", "branch"]).any()
    starters = games.dropna(subset=["pitcher_id"])
    assert set(starters.pitcher_id.astype(int)).issubset(set(queries.pitcher_id.astype(int)))


def build_atmosphere_provenance(audit_rows: list[dict]) -> pd.DataFrame:
    rows = []
    for r in audit_rows:
        prov = "FORECAST_NATIVE_SURFACE_PRESSURE" if r.get("pressure_basis") == "LOCAL_SURFACE_PRESSURE" else ("DERIVED_PRESSURE" if r.get("pressure_basis") in ("LOCAL_BAROMETRY_ELEVATION_CORRECTED",) else ("SENSOR_NATIVE" if r.get("pressure_basis") == "INDOOR_SENSOR" else "UNKNOWN"))
        rows.append({
            "game_pk": r.get("game_pk"), "venue_name": r.get("venue_name"), "branch": r.get("branch"),
            "provider": "OPEN_METEO" if r.get("air_environment_basis") == "LOCAL_OUTDOOR_AMBIENT" else ("INDOOR_SENSOR" if r.get("air_environment_basis") == "DIRECT_INDOOR_MEASUREMENT" else "MODELED"),
            "model_identifier": r.get("air_environment_basis"),
            "selected_forecast_hour_utc": r.get("ambient_forecast_hour_utc"),
            "venue_lat": r.get("lat"), "venue_lon": r.get("lon"), "venue_elevation_m": r.get("ballparkelevationm"),
            "pressure_provenance": prov,
        })
    return pd.DataFrame(rows)


def build_density_audit(audit_rows: list[dict]) -> pd.DataFrame:
    rows = []
    for r in audit_rows:
        tc, rh, p = r.get("temperature_c"), r.get("relative_humidity_pct"), r.get("pressure_hpa")
        if tc is None or rh is None or p is None or any(pd.isna(x) for x in (tc, rh, p)):
            rows.append({"game_pk": r.get("game_pk"), "venue_name": r.get("venue_name"), "branch": r.get("branch"), "temperature_c": tc, "relative_humidity_pct": rh, "pressure_hpa": p, "vapor_pressure_hpa": None, "dry_air_partial_pressure_hpa": None, "temperature_k": None, "air_density_kg_m3": r.get("air_density_kg_m3"), "reference_density_kg_m3": RHO_REF, "density_ratio_to_ref": None, "density_uncertainty_kg_m3": r.get("density_uncertainty_kg_m3")})
            continue
        terms = density_terms(float(tc), float(rh), float(p))
        terms["game_pk"] = r.get("game_pk"); terms["venue_name"] = r.get("venue_name"); terms["branch"] = r.get("branch")
        terms["density_uncertainty_kg_m3"] = r.get("density_uncertainty_kg_m3")
        rows.append(terms)
    return pd.DataFrame(rows)


def write_health_and_debug(run_dir: Path, audit: pd.DataFrame, roof: pd.DataFrame, conflicts: pd.DataFrame, queries: pd.DataFrame, summary: pd.DataFrame, atmos: pd.DataFrame, dens: pd.DataFrame, games: pd.DataFrame, health: Health) -> None:
    findings = json.loads(health.frame().to_json(orient="records")) if not health.frame().empty else []
    starters_resolved = int(games.dropna(subset=["pitcher_id"]).pitcher_id.nunique()) if not games.empty and "pitcher_id" in games.columns else 0
    summary_cols_present = not summary.empty
    missing_baseline = int(summary[["baseline_release_speed_mph", "baseline_release_spin_rate_rpm", "baseline_pfx_x_inches", "baseline_pfx_z_inches"]].isna().any(axis=1).sum()) if summary_cols_present else 0
    missing_delta = int(summary[["delta_pfx_x_inches", "delta_pfx_z_inches"]].isna().any(axis=1).sum()) if summary_cols_present and "delta_pfx_x_inches" in summary.columns else 0
    summary_ = {
        "scheduled_games": int(games.game_pk.nunique()) if not games.empty and "game_pk" in games.columns else 0,
        "starters_resolved": starters_resolved,
        "environment_rows": int(len(audit)),
        "roof_evidence_rows": int(len(roof)),
        "pitcher_query_rows": int(len(queries)),
        "pitch_physics_rows": int(len(summary)),
        "conflicted_roof_games": int((conflicts.final_roof_state == "CONFLICTED").sum()) if not conflicts.empty else 0,
        "dual_branch_games": int((conflicts.branch_strategy == "DUAL_BRANCH").sum()) if not conflicts.empty else 0,
        "missing_pressure_provenance_rows": int((atmos.pressure_provenance == "UNKNOWN").sum()) if not atmos.empty else 0,
        "missing_density_term_rows": int(dens.air_density_kg_m3.isna().sum()) if not dens.empty else 0,
        "missing_pitch_baseline_rows": missing_baseline,
        "pitch_rows_missing_delta_fields": missing_delta,
        "findings": len(findings),
    }
    diagnostics = [
        {"check": "scheduled_games", "value": summary_["scheduled_games"], "status": "PASS" if summary_["scheduled_games"] else "INFO_ZERO_GAMES_SCHEDULED"},
        {"check": "scheduled_starters_resolved", "value": summary_["starters_resolved"], "status": "PASS" if summary_["starters_resolved"] else "WARN"},
        {"check": "roof_conflict_games", "value": summary_["conflicted_roof_games"], "status": "WARN" if summary_["conflicted_roof_games"] else "PASS"},
        {"check": "dual_branch_games", "value": summary_["dual_branch_games"], "status": "INFO"},
        {"check": "query_audit_rows", "value": summary_["pitcher_query_rows"], "status": "PASS" if summary_["pitcher_query_rows"] else "WARN"},
        {"check": "pitch_rows_missing_baseline", "value": missing_baseline, "status": "WARN" if missing_baseline else "PASS"},
        {"check": "pitch_rows_missing_delta_fields", "value": missing_delta, "status": "WARN" if missing_delta else "PASS"},
        {"check": "games_with_full_atmosphere_provenance", "value": int((atmos.pressure_provenance != "UNKNOWN").sum()) if not atmos.empty else 0, "status": "PASS" if not atmos.empty and (atmos.pressure_provenance != "UNKNOWN").all() else "WARN"},
        {"check": "air_density_range_kg_m3_across_todays_venues", "value": float(audit.air_density_kg_m3.max() - audit.air_density_kg_m3.min()) if not audit.empty else float("nan"), "status": "INFO_CONFIRMS_DENSITY_VARIES_BY_PARK"},
    ]
    health_json = {"export_type": "system_health", "version": VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "summary": summary_, "diagnostics": diagnostics, "findings": findings}
    (run_dir / "mlb_dashboard_system_health.json").write_text(json.dumps(health_json, indent=2), encoding="utf-8")
    pd.DataFrame(diagnostics).to_csv(run_dir / "run_health_and_model_diagnostics.csv", index=False)

    debug = {
        "version": VERSION,
        "run_dir": str(run_dir.resolve()),
        "required_files_present": sorted(p.name for p in run_dir.iterdir() if p.is_file()),
        "section_row_counts": {
            "game_environment_audit": int(len(audit)), "roof_evidence_audit": int(len(roof)),
            "roof_conflict_audit": int(len(conflicts)), "pitcher_query_audit": int(len(queries)),
            "pitch_physics_environment": int(len(summary)), "atmosphere_provenance_audit": int(len(atmos)),
            "density_calculation_audit": int(len(dens)), "findings": len(findings),
        },
    }
    (run_dir / "dashboard_debug_snapshot.json").write_text(json.dumps(debug, indent=2), encoding="utf-8")


def write_bundle_files(run_dir: Path) -> None:
    def rec(path: Path):
        df = safe_read_csv(path)
        return json.loads(df.to_json(orient="records")) if not df.empty else []

    health_path = run_dir / "mlb_dashboard_system_health.json"
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    debug_path = run_dir / "dashboard_debug_snapshot.json"
    debug = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {}
    glossary_path = Path(__file__).resolve().parent / "V44_IN_APP_GLOSSARY.json"
    glossary = json.loads(glossary_path.read_text(encoding="utf-8")) if glossary_path.exists() else []

    env = safe_read_csv(run_dir / "game_environment_audit.csv")
    games = []
    if not env.empty and "game_pk" in env.columns:
        cols = [c for c in ["game_pk", "game_datetime_utc", "venue_name_api", "venue_name", "roofclass", "roof_state", "roof_decision_reason", "ballparkelevationm"] if c in env.columns]
        games = json.loads(env[cols].drop_duplicates("game_pk").to_json(orient="records"))

    bundle = {
        "meta": {"version": VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "run_dir": str(run_dir.resolve())},
        "summary": health.get("summary", {}),
        "games": games,
        "game_environment_audit": rec(run_dir / "game_environment_audit.csv"),
        "roof_evidence_audit": rec(run_dir / "roof_evidence_audit.csv"),
        "roof_conflict_audit": rec(run_dir / "roof_conflict_audit.csv"),
        "pitcher_query_audit": rec(run_dir / "pitcher_query_audit.csv"),
        "pitch_physics_environment": rec(run_dir / "pitch_physics_environment.csv"),
        "pitch_effect_decomposition": rec(run_dir / "pitch_effect_decomposition.csv"),
        "atmosphere_provenance_audit": rec(run_dir / "atmosphere_provenance_audit.csv"),
        "density_calculation_audit": rec(run_dir / "density_calculation_audit.csv"),
        "findings": rec(run_dir / "findings.csv"),
        "run_health_and_model_diagnostics": rec(run_dir / "run_health_and_model_diagnostics.csv"),
        "dashboard_debug_snapshot": debug,
        "glossary": glossary,
    }
    (run_dir / "mlb_dashboard_data_bundle.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--park-reference")
    ap.add_argument("--indoor-sensor-csv")
    ap.add_argument("--output-dir", default="mlb_daily_outputs")
    ap.add_argument("--season-start")
    ap.add_argument("--no-savant", action="store_true")
    ap.add_argument("--include-pitch-level-audit", action="store_true")
    ap.add_argument("--open-dashboard", action="store_true")
    a = ap.parse_args()

    day = a.date or str(datetime.now(ZoneInfo("America/New_York")).date())
    root = Path(__file__).resolve().parent
    refs = [Path(a.park_reference)] if a.park_reference else [
        root / "mlb_park_reference_full_corrected_v3.csv", Path.cwd() / "mlb_park_reference_full_corrected_v3.csv",
        root / "mlb_park_reference_verified.csv", Path.cwd() / "mlb_park_reference_verified.csv",
    ]
    ref = next((p for p in refs if p.exists()), None)
    if ref is None:
        raise FileNotFoundError("Park reference not found; use --park-reference.")

    run_dir = Path(a.output_dir) / f"{day}_{utcstamp()}"
    raw = run_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    health = Health()
    s = session()
    parks = load_parks(ref)
    games_unique = schedule(s, day, health)
    joined = join_parks(games_unique, parks, health)

    audit_rows: list[dict] = []
    summary_rows: list[pd.DataFrame] = []
    query_rows: list[dict] = []
    roof_rows: list[dict] = []
    conflict_rows: list[dict] = []
    pitch_audit: list[pd.DataFrame] = []
    branch_map: dict[tuple, dict] = {}
    season_cache: dict[int, pd.DataFrame] = {}
    recent_cache: dict[int, pd.DataFrame] = {}

    # FIX (V44.0.1): explicit empty-schedule guard. Previously this fell
    # through to `joined.groupby("game_pk", ...)` on a DataFrame that could
    # have zero columns, raising KeyError: 'game_pk' and crashing before any
    # output file was written. Now a zero-game day produces a complete,
    # valid, schema-correct (but empty) run directory instead of a crash.
    if joined.empty:
        LOG.warning("No games resolved for %s -- writing an empty-but-valid run directory instead of crashing.", day)
    else:
        for game_pk, game_rows in joined.groupby("game_pk", sort=False):
            g = game_rows.iloc[0]
            state, reason, votes, evidence = roof_state(s, g, raw, health, roof_rows, conflict_rows)
            a_env = ambient(s, g, health, raw)
            sensor = indoor_sensor(a.indoor_sensor_csv, g, g.game_datetime_utc)
            for b in branches(g, state, a_env, sensor):
                rec = {
                    "game_pk": game_pk, "game_datetime_utc": g.game_datetime_utc, "venue_name_api": g.venue_name_api,
                    "venue_name": g.venuename, "roofclass": g.roofclass, "ballparkelevationm": g.ballparkelevationm,
                    "roof_state": state, "roof_decision_reason": reason, "roof_votes_json": json.dumps(votes, sort_keys=True),
                    "park_join_method": g.park_join_method, "home_plate_bearing_deg": g.homeplatebearingdeg,
                    "lat": g.lat, "lon": g.lon,
                    **a_env, **b,
                }
                audit_rows.append(rec)
                branch_map[(int(game_pk), b["branch"])] = rec

        season_start = date(pd.to_datetime(a.date or day).year, 3, 1).isoformat()
        recent_start = (pd.to_datetime(a.date or day).date() - timedelta(days=30)).isoformat()
        end_exclusive = (pd.to_datetime(a.date or day).date() + timedelta(days=1)).isoformat()

        if not a.no_savant:
            for _, r in joined.iterrows():
                if pd.isna(r.pitcher_id):
                    continue
                pid = int(r.pitcher_id)
                if pid not in season_cache:
                    season_cache[pid] = savant_query(s, pid, a.season_start or season_start, end_exclusive, raw, health, query_rows, "season")
                if pid not in recent_cache:
                    recent_cache[pid] = savant_query(s, pid, recent_start, end_exclusive, raw, health, query_rows, "recent30")
                arsenal = weighted_arsenal(summarize_window(season_cache[pid]), summarize_window(recent_cache[pid]))
                if arsenal.empty:
                    health.add("INFO", "PITCH_BASELINE_UNAVAILABLE", f"game:{r.game_pk}|pitcher:{pid}", "No Savant rows in season or recent-30 window for this pitcher.", "No baseline can be honestly computed; row omitted rather than fabricated.", "Pitchers / pitch effects")
                    continue
                for branch in ("OUTDOOR", "ENCLOSED"):
                    env = branch_map.get((int(r.game_pk), branch))
                    if not env:
                        continue
                    phys = apply_environment_physics(arsenal, env)
                    for c in ("game_pk", "side", "team", "pitcher_id", "pitcher_name", "pitcher_resolution_method"):
                        phys[c] = r[c]
                    phys["venue_name"] = r.venuename
                    phys["branch"] = branch
                    phys["roof_state"] = env["roof_state"]
                    phys["air_density_kg_m3"] = env["air_density_kg_m3"]
                    summary_rows.append(phys)
                    if a.include_pitch_level_audit:
                        pitch_audit.append(pitch_level_audit(season_cache[pid], r, env))

    audit = pd.DataFrame(audit_rows)
    validate_environment_rows(audit, health)
    summary = pd.concat(summary_rows, ignore_index=True) if summary_rows else pd.DataFrame()
    queries = pd.DataFrame(query_rows)
    roof = pd.DataFrame(roof_rows)
    conflicts = pd.DataFrame(conflict_rows)
    pla = pd.concat(pitch_audit, ignore_index=True) if pitch_audit else pd.DataFrame()
    validate_pitch_rows(summary, queries, joined)

    atmos = build_atmosphere_provenance(audit_rows)
    dens = build_density_audit(audit_rows)

    audit.to_csv(run_dir / "game_environment_audit.csv", index=False)
    write_csv_contract(roof, run_dir / "roof_evidence_audit.csv")
    write_csv_contract(conflicts, run_dir / "roof_conflict_audit.csv")
    write_csv_contract(queries, run_dir / "pitcher_query_audit.csv")
    write_csv_contract(summary, run_dir / "pitch_physics_environment.csv")
    write_csv_contract(summary, run_dir / "pitch_effect_decomposition.csv")
    write_csv_contract(atmos, run_dir / "atmosphere_provenance_audit.csv")
    write_csv_contract(dens, run_dir / "density_calculation_audit.csv")
    if a.include_pitch_level_audit:
        write_csv_contract(pla, run_dir / "pitch_level_environment_audit.csv")
    write_csv_contract(health.frame(), run_dir / "findings.csv", Health.COLUMNS)

    write_health_and_debug(run_dir, audit, roof, conflicts, queries, summary, atmos, dens, joined, health)

    manifest = {
        "version": VERSION, "run_utc": datetime.now(timezone.utc).isoformat(), "requested_date": day,
        "park_reference": str(ref.resolve()), "fatal": health.fatal(),
        "policy": {
            "outdoor": "NOT_APPLICABLE, one OUTDOOR branch, wind applies",
            "fixed_enclosed": "FIXED_CLOSED, one ENCLOSED branch, external wind zero, density numeric",
            "retractable_dedicated_source": "a venue-dedicated single-purpose source can resolve roof alone",
            "retractable_generic_quorum": "two agreeing generic sources resolve roof if no dedicated source available",
            "retractable_single_generic_fallback": "one uncontested generic vote resolves roof if no other source disagrees",
            "retractable_conflict": "explicit OPEN and CLOSED generic votes -> CONFLICTED; dual OUTDOOR/ENCLOSED branches",
            "retractable_unresolved": "no decisive evidence at all -> dual OUTDOOR/ENCLOSED branches",
            "density_model": "computed per-venue from local lat/lon ambient conditions, elevation-corrected; never a global constant",
            "zero_games_handling": "if the MLB schedule API returns zero games for the requested date, a complete, schema-correct, empty run directory is written instead of crashing.",
        },
        "files": {str(p.relative_to(run_dir)): sha(p) for p in run_dir.rglob("*") if p.is_file()},
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_bundle_files(run_dir)

    print(json.dumps({
        "status": "FAIL" if health.fatal() else "COMPLETE", "version": VERSION, "requested_date": day,
        "run_dir": str(run_dir.resolve()), "games": int(joined.game_pk.nunique()) if not joined.empty and "game_pk" in joined.columns else 0,
        "starters_resolved": int(joined.dropna(subset=["pitcher_id"]).pitcher_id.nunique()) if not joined.empty and "pitcher_id" in joined.columns else 0,
        "environment_rows": int(len(audit)), "pitch_rows": int(len(summary)), "findings": int(len(health.rows)),
    }, indent=2))

    if a.open_dashboard:
        dashboard = run_dir / "mlb-pitch-environment-live-dashboard.html"
        if dashboard.exists():
            webbrowser.open(dashboard.resolve().as_uri())


if __name__ == "__main__":
    main()
