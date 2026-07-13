import csv, io, json, logging
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse

from app.services.fetcher import build_all_sports, build_sport
from app.services.diagnostics import write_run_diagnostic
from app.core.config import SPORT_KEYS, LOG_DIR

router     = APIRouter()
logger     = logging.getLogger(__name__)
_cache: dict              = {}
_cache_time: datetime | None = None
_TTL = 120

async def _get_cached():
    global _cache,_cache_time
    now = datetime.now()
    if _cache_time and (now-_cache_time).total_seconds()<_TTL and _cache:
        return _cache
    _cache      = await build_all_sports()
    _cache_time = now
    _write_log(_cache)
    write_run_diagnostic(_cache)
    return _cache

def _write_log(data):
    Path(LOG_DIR).mkdir(parents=True,exist_ok=True)
    lp = Path(LOG_DIR)/f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with open(lp,"a",encoding="utf-8") as f:
        f.write(json.dumps({"timestamp":datetime.now().isoformat(),"data":data})+"\n")

_HDR=["Sport","Matchup","Time","Away","Home",
      "Cons_Away_ML","Cons_Home_ML","Away_Raw%","Home_Raw%",
      "Away_NoVig%","Home_NoVig%","Power_Away%","Power_Home%",
      "Kalshi%","Kalshi_Disp%","Kalshi_EV%",
      "Poly%","Poly_Disp%","Poly_EV%",
      "Spread_Pt","Total_Pt","Avg_Vig%"]

def _row(sl,g):
    c=g.get("consensus",{});mh=g.get("implied_matrix_home",{});ma=g.get("implied_matrix_away",{})
    sp=g.get("spread",{});tot=g.get("totals",{})
    ch=c.get("home") or {};ca=c.get("away") or {}
    sh=list(sp.values())[0] if sp else {}
    ov=tot.get("Over") or {}
    vigs=[bd.get("vig_pct") for bd in g.get("per_book",{}).values() if bd.get("vig_pct") is not None]
    return [sl,g.get("title",""),g.get("commence",""),g.get("away",""),g.get("home",""),
            ca.get("american",""),ch.get("american",""),
            ca.get("raw_implied",""),ch.get("raw_implied",""),
            ca.get("no_vig_implied",""),ch.get("no_vig_implied",""),
            ma.get("power_odds",""),mh.get("power_odds",""),
            mh.get("kalshi",""),mh.get("kalshi_disparity",""),mh.get("kalshi_ev",""),
            mh.get("polymarket",""),mh.get("poly_disparity",""),mh.get("poly_ev",""),
            sh.get("point",""),ov.get("point",""),
            round(sum(vigs)/len(vigs),4) if vigs else ""]

def _to_csv(rows):
    buf=io.StringIO();w=csv.writer(buf);w.writerow(_HDR);w.writerows(rows);buf.seek(0);return buf

@router.get("/api/dashboard")
async def dashboard():   return await _get_cached()

@router.get("/api/dashboard/{sl}")
async def dash_sport(sl:str):
    sl=sl.lower()
    if sl not in SPORT_KEYS: raise HTTPException(404,f"Valid sports: {list(SPORT_KEYS)}")
    async with httpx.AsyncClient() as c: return await build_sport(sl,SPORT_KEYS[sl],c)

@router.get("/api/export/all")
async def exp_all():
    d=await _get_cached()
    rows=[_row(sl,g) for sl,sd in d.items() for g in sd.get("games",[])]
    date=datetime.now().strftime("%Y-%m-%d")
    return StreamingResponse(_to_csv(rows),media_type="text/csv",
        headers={"Content-Disposition":f"attachment; filename=overlineedge_all_{date}.csv"})

@router.get("/api/export/{sl}")
async def exp_sport(sl:str):
    sl=sl.lower();d=await _get_cached()
    rows=[_row(sl,g) for g in d.get(sl,{}).get("games",[])]
    date=datetime.now().strftime("%Y-%m-%d")
    return StreamingResponse(_to_csv(rows),media_type="text/csv",
        headers={"Content-Disposition":f"attachment; filename=overlineedge_{sl}_{date}.csv"})

@router.get("/api/logs/{date}")
async def get_log(date:str):
    lp=Path(LOG_DIR)/f"{date}.jsonl"
    if not lp.exists(): raise HTTPException(404,f"No log for {date}")
    entries=[]
    with open(lp,encoding="utf-8") as f:
        for line in f:
            try: entries.append(json.loads(line))
            except Exception: pass
    return {"date":date,"entries":len(entries),"log":entries}

@router.get("/api/health")
async def health():
    age=int((datetime.now()-_cache_time).total_seconds()) if _cache_time else None
    return {"status":"ok","cache_age_seconds":age,"sports":list(_cache.keys()) if _cache else []}


# ============================================================
# ADDED: shareable log/error export — download instead of copy-paste
# ============================================================
# Open in your browser while the server is running to download a plain
# text file of the FULL server log (startup lines, scrape errors,
# asyncio tracebacks — everything main.py writes). Attach/paste THAT
# file here instead of copying terminal text by hand.
#
#   http://127.0.0.1:8000/api/logs/server/download
#
@router.get("/api/logs/server/download")
async def download_server_log():
    lp = Path("data/logs/server.log")
    if not lp.exists():
        return PlainTextResponse(
            "No server.log file found yet. Start the server (run.bat) and "
            "let it run for a bit, then try this URL again.",
            media_type="text/plain",
        )
    text = lp.read_text(encoding="utf-8", errors="replace")
    date = datetime.now().strftime("%Y-%m-%d")
    return PlainTextResponse(
        text,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=overlineedge_server_log_{date}.txt"},
    )

@router.get("/api/diagnostics/latest")
async def latest_diagnostic():
    files = sorted(Path("data/diagnostics").glob("OverlineEdge_RunDiagnostic_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files: raise HTTPException(404, "No run diagnostic has been generated yet")
    return {"file": files[0].name, "path": str(files[0]), "created_at": datetime.fromtimestamp(files[0].stat().st_mtime).isoformat()}
