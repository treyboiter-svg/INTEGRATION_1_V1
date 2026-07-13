"""fetcher.py — OverlineEdge v8 (Python 3.14 compatible)"""
from __future__ import annotations
import asyncio, concurrent.futures, hashlib, json, logging, re
from datetime import datetime

import httpx, pytz

from app.core.config import KALSHI_BASE, KALSHI_SERIES, POLY_GAMMA, POLY_SERIES, SPORT_KEYS
from app.services.normalizer import normalize_team
from app.services.venue_resolver import VenueResolver, default_workbook_path, _fold, _tokens
from app.services.odds_calc import (
    american_to_implied, remove_vig_proportional, remove_vig_power,
    implied_to_american, build_implied_matrix, power_odds, vig_percent,
)
from app.services.odds_scraper import scrape_sport

logger = logging.getLogger(__name__)
_RESOLVER: VenueResolver | None = None

def _resolver() -> VenueResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = VenueResolver(default_workbook_path())
        logger.info("Venue authority loaded: %s rows", len(_RESOLVER.rows))
    return _RESOLVER
ET_TZ  = pytz.timezone("America/New_York")
_SEM   = asyncio.Semaphore(2)
_POOL  = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_BOOKS = [
    "draftkings","fanduel","betmgm","caesars",
    "pointsbet","bovada","bet365","betrivers","superbook","unibet",
]


_AMERICAN_RE = re.compile(r"(?<![\d.])[+-](?:100|[1-9]\d{2,3})(?![\d.])")
_NUMBER_RE = re.compile(r"(?<![\d.])[+-]?\d+(?:\.\d+)?(?![\d.])")

def _valid_american(value):
    try: value = int(value)
    except (TypeError, ValueError): return False
    return (100 <= value <= 9999) or (-9999 <= value <= -100)

def _evidence_strings(book):
    """Yield text plus semantic metadata from the exact odds-cell DOM."""
    yield str(book.get("raw") or ""), "cell_text"
    for key in ("value", "moneyline"):
        if book.get(key) is not None: yield str(book[key]), key
    for node in book.get("nodes") or []:
        semantic = " ".join([str(node.get("class") or ""), *[f"{k}={v}" for k,v in (node.get("attrs") or {}).items()]]).lower()
        yield str(node.get("text") or ""), semantic
        for k,v in (node.get("attrs") or {}).items(): yield str(v), f"{semantic} {k}".lower()
    for k,v in (book.get("attrs") or {}).items(): yield str(v), f"cell {k}".lower()

def _extract_american(book):
    """Deterministic price extraction: structured price fields first, signed text second.
    Unsigned numbers are accepted only from a field explicitly labeled odds/price/moneyline."""
    candidates=[]
    for text, semantic in _evidence_strings(book):
        semantic_price = any(x in semantic for x in ("moneyline","american","odds","price","data-ml"))
        for token in _AMERICAN_RE.findall(text.replace("−", "-")):
            candidates.append((int(token), 3 if semantic_price else 1, "signed_semantic" if semantic_price else "signed_cell_text"))
        if semantic_price:
            # Do not duplicate a signed value as unsigned: that would erase its sign.
            unsigned_text = _AMERICAN_RE.sub("", text.replace("−", "-"))
            for token in re.findall(r"(?<![\d.])(?:100|[1-9]\d{2,3})(?![\d.])", unsigned_text):
                value=int(token)
                if _valid_american(value): candidates.append((value, 4, "unsigned_explicit_price_field"))
    if not candidates: return None, "missing_explicit_price"
    best=max(x[1] for x in candidates); values={x[0] for x in candidates if x[1]==best}
    if len(values)!=1: return None, "ambiguous_price_candidates"
    value=values.pop()
    return value, next(x[2] for x in candidates if x[0]==value and x[1]==best)

def _extract_point(book, bet_type):
    """Extract line only from the line/value context, never from a price field."""
    candidates=[]
    for text, semantic in _evidence_strings(book):
        if any(x in semantic for x in ("moneyline","american","odds","price")): continue
        lower=text.lower().replace("−","-")
        if bet_type=="total":
            m=re.search(r"(?:over|under|\bo\b|\bu\b)\s*([0-9]+(?:\.5)?)",lower)
            if m: candidates.append(float(m.group(1)))
        else:
            for token in _NUMBER_RE.findall(lower):
                v=float(token)
                if abs(v)<100: candidates.append(v)
    if not candidates:
        # Visible cell text often contains both line and price: first sub-100 token is the line.
        for token in _NUMBER_RE.findall(str(book.get("raw") or "").replace("−","-")):
            v=float(token)
            if abs(v)<100: candidates.append(v); break
    return candidates[0] if candidates else None

def _parse_market_cell(book, bet_type):
    price, reason = _extract_american(book)
    point = None if bet_type=="moneyline" else _extract_point(book, bet_type)
    if bet_type!="moneyline" and point is None:
        return {"point":None,"american":price,"parse_status":"rejected_missing_line","price_evidence":reason}
    return {"point":point,"american":price,"parse_status":"accepted" if price is not None else "rejected_"+reason,"price_evidence":reason}

def _book_payload(bk):
    return " ".join(str(bk.get(k) or "") for k in ("value", "moneyline", "raw")).strip()


def _build_book_map(scrape_results, sport=""):
    merged = {}
    for bt, games in scrape_results.items():
        for g in games:
            teams = g.get("teams",[])
            if len(teams)<2: continue
            resolved = _resolver().validate_game(sport, teams[0].get("name", ""), teams[1].get("name", ""))
            if not resolved["accepted"]:
                logger.warning("Rejected sportsbook row [%s]: away=%r home=%r reason=%s scores=(%.3f, %.3f)", sport, teams[0].get("name", ""), teams[1].get("name", ""), resolved["reason"], resolved["away_score"], resolved["home_score"])
                continue
            away = resolved["away"].team
            home = resolved["home"].team
            key  = (away,home)
            merged.setdefault(key,{"time":g.get("time",""),"books":{}})
            for si,tm in enumerate(teams):
                for bk in tm.get("books",[]):
                    idx   = bk.get("index",0)
                    entry = merged[key]["books"].setdefault(idx,{})
                    val   = bk.get("value")
                    ml    = bk.get("moneyline")
                    raw   = _book_payload(bk)
                    ih    = si==1
                    if bt=="moneyline":
                        entry["ml_home" if ih else "ml_away"] = _parse_market_cell(bk, "moneyline")
                    elif bt=="spread":
                        entry["spread_home" if ih else "spread_away"] = _parse_market_cell(bk, "spread")
                    elif bt=="total":
                        entry["total_over" if si==0 else "total_under"] = _parse_market_cell(bk, "total")
    return merged


def _aggregate_books(books_by_idx):
    per_book = {}
    home_raw_list, away_raw_list = [], []
    home_nv_list,  away_nv_list  = [], []

    for idx, bd in books_by_idx.items():
        bname   = _BOOKS[idx] if idx<len(_BOOKS) else f"book_{idx}"
        h_cell  = bd.get("ml_home") or {}
        a_cell  = bd.get("ml_away") or {}
        h_am    = h_cell.get("american") if isinstance(h_cell, dict) else h_cell
        a_am    = a_cell.get("american") if isinstance(a_cell, dict) else a_cell
        h_raw   = american_to_implied(h_am)
        a_raw   = american_to_implied(a_am)

        if h_raw is not None and a_raw is not None:
            h_prop, a_prop   = remove_vig_proportional(h_raw, a_raw)
            h_power, a_power = remove_vig_power(h_raw, a_raw)
        else:
            h_prop = a_prop = h_power = a_power = None

        entry = {
            "home_american":  h_am,   "away_american":  a_am,
            "home_raw":       h_raw,  "away_raw":       a_raw,
            "home_nv_prop":   h_prop, "away_nv_prop":   a_prop,
            "home_nv_power":  h_power,"away_nv_power":  a_power,
            "vig_pct":        vig_percent(h_raw,a_raw),
            "moneyline_parse": {"home": h_cell if isinstance(h_cell, dict) else {}, "away": a_cell if isinstance(a_cell, dict) else {}},
            "spreads":{}, "totals":{},
        }
        for side,rk in [("home","spread_home"),("away","spread_away")]:
            sd = bd.get(rk)
            if sd:
                entry["spreads"][side]={
                    "point":sd.get("point"),"american":sd.get("american"), "parse_status": sd.get("parse_status"), "price_evidence": sd.get("price_evidence"),
                    "implied":american_to_implied(sd.get("american")),
                }
        for side,rk in [("Over","total_over"),("Under","total_under")]:
            td = bd.get(rk)
            if td:
                entry["totals"][side]={
                    "point":td.get("point"),"american":td.get("american"), "parse_status": td.get("parse_status"), "price_evidence": td.get("price_evidence"),
                    "implied":american_to_implied(td.get("american")),
                }
        per_book[bname] = entry
        if h_raw  is not None: home_raw_list.append(h_raw)
        if a_raw  is not None: away_raw_list.append(a_raw)
        if h_prop is not None: home_nv_list.append(h_prop)
        if a_prop is not None: away_nv_list.append(a_prop)

    raw_h = round(sum(home_raw_list)/len(home_raw_list),4) if home_raw_list else None
    raw_a = round(sum(away_raw_list)/len(away_raw_list),4) if away_raw_list else None
    nv_h  = round(sum(home_nv_list) /len(home_nv_list), 4) if home_nv_list  else None
    nv_a  = round(sum(away_nv_list) /len(away_nv_list), 4) if away_nv_list  else None

    consensus = {
        "home":{"raw_implied":raw_h,"no_vig_implied":nv_h,"american":implied_to_american(nv_h)},
        "away":{"raw_implied":raw_a,"no_vig_implied":nv_a,"american":implied_to_american(nv_a)},
    }
    sp_agg, tot_agg = {}, {}
    for bd in per_book.values():
        for s,sd in bd.get("spreads",{}).items():
            if sd.get("implied"): sp_agg.setdefault(s,[]).append(sd["implied"])
        for s,td in bd.get("totals",{}).items():
            if td.get("implied"): tot_agg.setdefault(s,[]).append(td["implied"])
    sp_cons  = {s:{"implied":round(sum(v)/len(v),4)} for s,v in sp_agg.items()  if v}
    tot_cons = {s:{"implied":round(sum(v)/len(v),4)} for s,v in tot_agg.items() if v}
    return per_book, consensus, sp_cons, tot_cons


async def _fetch_kalshi(sport_label, client):
    markets = []
    for series in KALSHI_SERIES.get(sport_label,[]):
        try:
            r = await client.get(f"{KALSHI_BASE}/markets",
                                 params={"series_ticker":series,"status":"open","limit":200},
                                 timeout=15)
            r.raise_for_status()
            markets.extend(r.json().get("markets",[]))
        except Exception as e:
            logger.warning(f"Kalshi ({series}): {e}")
    return markets


def _parse_kalshi(markets):
    """Keep title, subtitle and ticker context; retain last trade when no quote exists."""
    result = {}
    for m in markets:
        title = str(m.get("title") or "")
        subtitle = str(m.get("subtitle") or "")
        ticker = str(m.get("ticker") or m.get("market_ticker") or "")
        candidate = " | ".join(x for x in (title, subtitle, ticker) if x)
        bid, ask, last = m.get("yes_bid"), m.get("yes_ask"), m.get("last_price")
        if bid is None and ask is None and last is None: continue
        mid = (bid + ask) / 2 if bid is not None and ask is not None else (bid if bid is not None else (ask if ask is not None else last))
        if mid is not None and mid <= 1: mid *= 100
        implied = round(float(mid), 4) if mid is not None else None
        entry = {"home_implied": implied, "american": implied_to_american(implied) if implied else None,
                 "title": title or subtitle, "subtitle": subtitle, "ticker": ticker,
                 "volume": m.get("volume") or m.get("dollar_volume")}
        if candidate: result[candidate.lower()] = entry
    return result


async def _fetch_poly(sport_label, client):
    sid = POLY_SERIES.get(sport_label)
    if sid is None: return []
    try:
        r = await client.get(f"{POLY_GAMMA}/events", params={"series_id":sid,"active":"true","closed":"false","limit":200}, timeout=15)
        r.raise_for_status(); payload = r.json()
        events = payload if isinstance(payload, list) else payload.get("events", [])
    except Exception as e:
        logger.warning("Polymarket (%s): %s", sport_label, e); return []
    markets = []
    for ev in events:
        context = " | ".join(str(ev.get(k) or "") for k in ("title", "slug", "ticker"))
        for market in ev.get("markets", []):
            row = dict(market); row["_event_context"] = context; row["_event_title"] = ev.get("title") or ""
            markets.append(row)
    return markets


def _parse_poly(markets):
    """Preserve raw binary market semantics; price ownership is resolved after team matching."""
    result = {}
    for m in markets:
        question = str(m.get("question") or "")
        context = str(m.get("_event_context") or "")
        outcomes, prices = m.get("outcomes") or [], m.get("outcomePrices") or []
        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except Exception: outcomes = []
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except Exception: prices = []
        if len(outcomes) < 2 or len(prices) < 2: continue
        try: values = [round(float(x) * 100, 4) for x in prices[:2]]
        except (TypeError, ValueError): continue
        candidate = " | ".join(x for x in (context, question, " ".join(map(str, outcomes))) if x)
        if candidate:
            result[candidate.lower()] = {"question": question, "event_title": m.get("_event_title") or "",
                "outcomes": list(map(str, outcomes[:2])), "outcome_prices": values,
                "market_id": m.get("id") or m.get("conditionId") or ""}
    return result


def _poly_for_game(raw, identity):
    """Map Polymarket prices to teams only when the YES/outcome label identifies a canonical side.
    Unknown semantics stay null; this prevents silent away/home inversion."""
    if not raw: return {}
    away, home = identity["away"].team, identity["home"].team
    text = " | ".join([raw.get("question", ""), *raw.get("outcomes", [])])
    # Resolve each outcome label individually. A team-named outcome gets its own price.
    resolved = []
    for label, price in zip(raw.get("outcomes", []), raw.get("outcome_prices", [])):
        team, score, _ = _resolver().resolve_team(identity["home"].league, label)
        resolved.append((team.team if team and score >= 0.90 else None, price))
    by_team = {team: price for team, price in resolved if team}
    # Standard Yes/No market: infer the subject only from canonical team mention in question/context.
    if not by_team and {x.lower() for x in raw.get("outcomes", [])} >= {"yes", "no"}:
        # A Yes/No price only belongs to a side when the question explicitly names one subject.
        # If both teams only appear in shared event context, price ownership is ambiguous and remains null.
        q = raw.get("question", "")
        away_hit = _fold(away) in _fold(q) or (_tokens(away) <= _tokens(q))
        home_hit = _fold(home) in _fold(q) or (_tokens(home) <= _tokens(q))
        yes_price = raw["outcome_prices"][next(i for i, x in enumerate(raw["outcomes"]) if x.lower() == "yes")]
        if home_hit and not away_hit:
            by_team[home], by_team[away] = yes_price, round(100 - yes_price, 4)
        elif away_hit and not home_hit:
            by_team[away], by_team[home] = yes_price, round(100 - yes_price, 4)
    hp, ap = by_team.get(home), by_team.get(away)
    if hp is None or ap is None: return {}
    return {"home_implied": hp, "away_implied": ap, "american": implied_to_american(hp),
            "question": raw.get("question"), "event_title": raw.get("event_title"), "outcomes": raw.get("outcomes"),
            "price_mapping": "explicit_outcome_or_verified_yes_subject"}


async def build_sport(sport_label, sport_slug, client):
    loop = asyncio.get_running_loop()
    async with _SEM:
        scrape_results,_ = await loop.run_in_executor(_POOL,scrape_sport,sport_slug)
    book_map = _build_book_map(scrape_results,sport_label)
    kal_raw, poly_raw = await asyncio.gather(
        _fetch_kalshi(sport_label,client),
        _fetch_poly(sport_label,client),
    )
    kal_map  = _parse_kalshi(kal_raw)
    poly_map = _parse_poly(poly_raw)
    games    = []
    date_str = datetime.now(ET_TZ).strftime("%Y-%m-%d")
    for (away,home), gdata in book_map.items():
        pb,cons,sp,tot = _aggregate_books(gdata.get("books",{}))
        missing_prices = {"moneyline": [], "spread": [], "total": []}
        for book_name, book_data in pb.items():
            for side, value in book_data.get("moneyline_parse", {}).items():
                if value.get("american") is None: missing_prices["moneyline"].append({"book": book_name, "side": side, "line": None, "reason": value.get("parse_status", "source_price_not_exposed"), "evidence": value.get("price_evidence")})
            for side, value in book_data.get("spreads", {}).items():
                if value.get("american") is None: missing_prices["spread"].append({"book": book_name, "side": side, "line": value.get("point"), "reason": "source_price_not_exposed"})
            for side, value in book_data.get("totals", {}).items():
                if value.get("american") is None: missing_prices["total"].append({"book": book_name, "side": side, "line": value.get("point"), "reason": "source_price_not_exposed"})
        identity = _resolver().validate_game(sport_label, away, home)
        if not identity["accepted"]:
            logger.error("Invariant violation: invalid assembled game [%s] %s @ %s", sport_label, away, home)
            continue
        km, kalshi_match_score, kalshi_match_method = _resolver().match_market(identity, kal_map.keys())
        pm, poly_match_score, poly_match_method = _resolver().match_market(identity, poly_map.keys())
        kal = kal_map.get(km,{}) if km else {}
        pol = _poly_for_game(poly_map.get(pm, {}), identity) if pm else {}
        if pm and not pol:
            poly_match_method = "rejected_ambiguous_outcome_side"
        nv_h  = (cons.get("home") or {}).get("no_vig_implied")
        nv_a  = (cons.get("away") or {}).get("no_vig_implied")
        pow_h = power_odds([bd.get("home_nv_prop") for bd in pb.values()])
        pow_a = power_odds([bd.get("away_nv_prop") for bd in pb.values()])
        k_h   = kal.get("home_implied")
        p_h   = pol.get("home_implied")
        k_a   = round(100-k_h,4) if k_h is not None else None
        p_a   = pol.get("away_implied")
        gid   = hashlib.sha1(f"{sport_label}|{away}|{home}|{date_str}".encode()).hexdigest()[:12]
        games.append({
            "game_id":gid,"sport":sport_label,
            "title":f"{away} @ {home}","home":home,"away":away,
            "commence":gdata.get("time",""),"status":"scheduled",
            "per_book":pb,"spread":sp,"totals":tot,"consensus":cons,
            "data_quality": {"missing_prices": missing_prices, "rule": "no_imputation"},
            "venue": {"name": identity["home"].venue, "city": identity["home"].city, "state": identity["home"].state, "lat": identity["home"].lat, "lon": identity["home"].lon, "elevation": identity["home"].elevation, "orientation_deg": identity["home"].orientation_deg, "orientation_label": identity["home"].orientation_label, "orientation_confidence": identity["home"].orientation_confidence},
            "match_audit": {"away_score": identity["away_score"], "home_score": identity["home_score"], "kalshi_score": kalshi_match_score, "kalshi_method": kalshi_match_method, "polymarket_score": poly_match_score, "polymarket_method": poly_match_method},
            "kalshi":kal or {"home_implied":None,"american":None,"title":None,"volume":None},
            "polymarket":pol or {"home_implied":None,"away_implied":None,"american":None,"question":None},
            "implied_matrix_home":build_implied_matrix(nv_h,k_h,p_h,pow_h),
            "implied_matrix_away":build_implied_matrix(nv_a,k_a,p_a,pow_a),
        })
    return {"games":games,"game_count":len(games),"last_updated":datetime.now().isoformat()}


async def build_all_sports():
    dashboard = {}
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[build_sport(lbl,slug,client) for lbl,slug in SPORT_KEYS.items()],
            return_exceptions=True,
        )
        for lbl,res in zip(SPORT_KEYS.keys(),results):
            if isinstance(res,Exception):
                logger.error(f"build_sport [{lbl}]: {res}",exc_info=res)
                dashboard[lbl]={"games":[],"game_count":0,"last_updated":datetime.now().isoformat()}
            else:
                dashboard[lbl]=res
    return dashboard
