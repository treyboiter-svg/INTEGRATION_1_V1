"""
_scrape_worker.py — OverlineEdge v8

Standalone subprocess worker. Runs in a FRESH Python interpreter process,
spawned via subprocess.run() from odds_scraper.py.

WHY THIS FILE EXISTS (root cause of the recurring NotImplementedError):
-------------------------------------------------------------------------
Playwright's SYNC API internally creates its own asyncio event loop via
asyncio.new_event_loop() to drive its driver connection (see the traceback:
"self.loop.run_until_complete(self.connection.run_async())"). new_event_loop()
consults the CURRENT asyncio EVENT LOOP POLICY to decide which loop class to
build.

Critically, asyncio's event loop POLICY is a process-wide global in Python's
asyncio module — NOT thread-local. Only the loop INSTANCE is thread-local.
So even installing a ProactorEventLoop instance on a worker thread (the
previous fix) does nothing here, because Playwright ignores any
already-set loop and asks the (still Selector) policy for a brand new one.

uvicorn --reload sets WindowsSelectorEventLoopPolicy process-wide, and
SelectorEventLoop has NO subprocess support on Windows (only ProactorEventLoop
implements _make_subprocess_transport) — hence the crash, no matter which
thread runs it, as long as it's in-process.

THE ACTUAL FIX: run Playwright in a completely SEPARATE PROCESS instead of a
thread. A subprocess gets its own fresh Python interpreter with its own
default event loop policy (Proactor, Windows' default since Python 3.8),
totally unaffected by whatever uvicorn changed in the parent process. This
file IS that separate process. It has zero dependency on the FastAPI app,
asyncio, or anything from the parent process's state — it just scrapes and
prints JSON to stdout.
"""
import json
import os
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import pytz

BASE_URL  = "https://www.scoresandodds.com"
BET_TYPES = ["spread", "total", "moneyline"]
HEADLESS  = os.getenv("HEADLESS", "1") != "0"
ET_TZ     = pytz.timezone("America/New_York")


def _clean_name(raw: str) -> str:
    """Preserve spaces; remove attached MLB pitcher notation and record noise."""
    s = str(raw or "").strip()
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = re.sub(r"\s+[A-Za-z.'-]+\([LRlr]\)\s*$", "", s)
    s = re.sub(r"\s*\d{1,3}[-\u2013]\d{1,3}(?:[-\u2013]\d{1,3})?\s*$", "", s)
    s = re.sub(r"^#?\s*\d+\s+", "", s)
    return re.sub(r"\s+", " ", s).strip()

def is_today(time_str: str) -> bool:
    try:
        now = datetime.now(ET_TZ).date()
        s   = (time_str or "").strip().lower()
        if "today" in s: return True
        m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", s)
        if m: return int(m.group(1)) == now.month and int(m.group(2)) == now.day
    except Exception:
        pass
    return False


def _click_market(page, bet_type):
    label = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}[bet_type]
    for sel in [f"a:has-text('{label}')", f"button:has-text('{label}')",
                f"li:has-text('{label}')", f"[data-market='{bet_type}']"]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=1500)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue


def parse_table(html, sport, bet_type):
    soup   = BeautifulSoup(html, "html.parser")
    prefix = f"odds-table-{bet_type}--"
    tbody  = soup.find("tbody", id=lambda x: x and prefix in x)
    if not tbody: return []
    games, cur = [], None
    for row in tbody.find_all("tr", recursive=False):
        tc = row.find("td", class_="game-time")
        if tc:
            if cur and cur["teams"]: games.append(cur)
            cur = {"sport": sport, "time": tc.get_text(strip=True),
                   "is_today": is_today(tc.get_text(strip=True)), "teams": []}
        team_cell = row.find("td", class_="game-team")
        if team_cell and cur is not None:
            t = {}
            ns = team_cell.find("span", class_="team-name")
            if ns: t["name"] = _clean_name(ns.get_text(" ", strip=True))
            books = []
            for idx, cell in enumerate(row.find_all("td", class_="game-odds")):
                bk  = {"index": idx}
                val = cell.find("span", class_="data-value")
                ml  = cell.find("span", class_="data-moneyline")
                if val: bk["value"]     = val.get_text(strip=True)
                if ml:  bk["moneyline"] = ml.get_text(strip=True)
                if "value" in bk or "moneyline" in bk: books.append(bk)
            t["books"] = books
            cur["teams"].append(t)
    if cur and cur["teams"]: games.append(cur)
    return [g for g in games if g.get("is_today", False)]


def _scrape(sport: str):
    url     = f"{BASE_URL}/{sport.strip().lower()}/odds"
    results = {bt: [] for bt in BET_TYPES}
    page    = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        try:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            for bt in BET_TYPES:
                _click_market(page, bt)
                page.wait_for_timeout(400)
                results[bt] = parse_table(page.content(), sport, bt)
        except Exception:
            if page is not None:
                try:
                    os.makedirs("data/debug_html", exist_ok=True)
                    with open(f"data/debug_html/{sport}_error.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                except Exception:
                    pass
        finally:
            browser.close()
    return results


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"results": {bt: [] for bt in BET_TYPES}, "count": 0, "error": "no sport arg"}))
        return
    sport = sys.argv[1]
    try:
        results = _scrape(sport)
    except Exception as e:
        results = {bt: [] for bt in BET_TYPES}
        print(json.dumps({"results": results, "count": 0, "error": str(e)}))
        return
    count = max((len(results[bt]) for bt in BET_TYPES), default=0)
    print(json.dumps({"results": results, "count": count}))


if __name__ == "__main__":
    main()
