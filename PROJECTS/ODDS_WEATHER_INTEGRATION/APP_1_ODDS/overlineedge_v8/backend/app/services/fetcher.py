"""fetcher.py — OverlineEdge v8 (Python 3.14 compatible)"""
from __future__ import annotations
import asyncio, concurrent.futures, hashlib, logging, re
from datetime import datetime

import httpx, pytz

from app.core.config import KALSHI_BASE, KALSHI_SERIES, POLY_GAMMA, POLY_SERIES, SPORT_KEYS
from app.services.normalizer import normalize_team
from app.services.venue_resolver import VenueResolver, default_workbook_path
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


def _parse_american(raw):
    if raw is None: return None
    m = re.search(r"[+-]?\d+",str(raw))
    return int(m.group(0)) if m else None


def _parse_pt_ml(raw):
    if raw is None: return None
    s   = str(raw)
    pts = re.search(r"[+-]?\d+(?:\.\d+)?",s)
    ml  = re.search(r"[+-]\d{3,}",s)
    return {"point":float(pts.group(0)) if pts else None,
            "american":int(ml.group(0)) if ml else None}


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
                    ih    = si==1
                    if bt=="moneyline":
                        entry["ml_home" if ih else "ml_away"] = _parse_american(ml or val)
                    elif bt=="spread":
                        entry["spread_home" if ih else "spread_away"] = _parse_pt_ml(f"{val or ''} {ml or ''}".strip())
                    elif bt=="total":
                        entry["total_over" if si==0 else "total_under"] = _parse_pt_ml(f"{val or ''} {ml or ''}".strip())
    return merged


def _aggregate_books(books_by_idx):
    per_book = {}
    home_raw_list, away_raw_list = [], []
    home_nv_list,  away_nv_list  = [], []

    for idx, bd in books_by_idx.items():
        bname   = _BOOKS[idx] if idx<len(_BOOKS) else f"book_{idx}"
        h_am    = bd.get("ml_home")
        a_am    = bd.get("ml_away")
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
            "spreads":{}, "totals":{},
        }
        for side,rk in [("home","spread_home"),("away","spread_away")]:
            sd = bd.get(rk)
            if sd:
                entry["spreads"][side]={
                    "point":sd.get("point"),"american":sd.get("american"),
                    "implied":american_to_implied(sd.get("american")),
                }
        for side,rk in [("Over","total_over"),("Under","total_under")]:
            td = bd.get(rk)
            if td:
                entry["totals"][side]={
                    "point":td.get("point"),"american":td.get("american"),
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
    result = {}
    for m in markets:
        title   = m.get("title") or m.get("subtitle") or ""
        yes_bid = m.get("yes_bid")
        yes_ask = m.get("yes_ask")
        if yes_bid is None and yes_ask is None: continue
        mid = (yes_bid+yes_ask)/2 if (yes_bid is not None and yes_ask is not None) \
              else (yes_bid if yes_bid is not None else yes_ask)
        if mid is not None and mid<1.0: mid=mid*100.0
        implied = round(mid,4) if mid else None
        result[title.lower()] = {
            "home_implied":implied,
            "american":implied_to_american(implied) if implied else None,
            "title":title,"volume":m.get("volume") or m.get("dollar_volume"),
        }
    return result


async def _fetch_poly(sport_label, client):
    sid = POLY_SERIES.get(sport_label)
    if sid is None: return []
    try:
        r = await client.get(f"{POLY_GAMMA}/events",
                             params={"series_id":sid,"active":"true","closed":"false","limit":200},
                             timeout=15)
        r.raise_for_status()
        payload = r.json()
        events  = payload if isinstance(payload,list) else payload.get("events",[])
    except Exception as e:
        logger.warning(f"Polymarket ({sport_label}): {e}")
        return []
    markets = []
    for ev in events:
        markets.extend(ev.get("markets",[]))
    return markets


def _parse_poly(markets):
    result = {}
    for m in markets:
        q      = m.get("question") or ""
        prices = m.get("outcomePrices") or []
        outs   = m.get("outcomes")      or []
        if len(outs)<2 or len(prices)<2: continue
        try:
            hp = float(prices[0])*100
            ap = float(prices[1])*100
        except Exception:
            continue
        result[q.lower()] = {
            "home_implied":round(hp,4),"away_implied":round(ap,4),
            "american":implied_to_american(hp),"question":q,
        }
    return result


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
        identity = _resolver().validate_game(sport_label, away, home)
        if not identity["accepted"]:
            logger.error("Invariant violation: invalid assembled game [%s] %s @ %s", sport_label, away, home)
            continue
        km, kalshi_match_score, kalshi_match_method = _resolver().match_market(identity, kal_map.keys())
        pm, poly_match_score, poly_match_method = _resolver().match_market(identity, poly_map.keys())
        kal = kal_map.get(km,{}) if km else {}
        pol = poly_map.get(pm,{}) if pm else {}
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
