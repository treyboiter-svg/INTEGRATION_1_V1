"""
odds_scraper.py — OverlineEdge v8 (Python 3.14 compatible, Windows-safe)

WHY THIS VERSION IS DIFFERENT FROM THE PREVIOUS PATCH:
---------------------------------------------------------
The previous fix tried to force a ProactorEventLoop onto the scraper's
worker THREAD. That did not work, because asyncio's event loop POLICY
(which decides what kind of loop Playwright's sync API builds internally)
is a process-wide global, not thread-local. uvicorn --reload sets
WindowsSelectorEventLoopPolicy for the whole process, and no per-thread
trick can override that for code (like Playwright) that asks the policy
for a brand-new loop.

THE ACTUAL FIX: run Playwright in a completely separate PROCESS via
subprocess.run(), calling _scrape_worker.py with a fresh python.exe
interpreter. A new process gets its own default event loop policy
(Proactor on Windows), totally isolated from whatever uvicorn's process
configured. This guarantees Playwright's driver subprocess can launch,
because Proactor DOES implement subprocess support on Windows.
"""
import json
import os
import subprocess
import sys

BET_TYPES = ["spread", "total", "moneyline"]

_WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_scrape_worker.py")


def scrape_sport(sport: str):
    """
    Spawns _scrape_worker.py as an isolated subprocess and reads back its
    JSON stdout. This function itself stays fully synchronous — it is called
    from fetcher.py inside loop.run_in_executor(), so blocking here is fine
    and expected.
    """
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER_PATH, sport],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {bt: [] for bt in BET_TYPES}, 0
    except Exception:
        return {bt: [] for bt in BET_TYPES}, 0

    if proc.returncode != 0 or not proc.stdout.strip():
        return {bt: [] for bt in BET_TYPES}, 0

    try:
        # Worker may print multiple lines (e.g. warnings) before the final
        # JSON line — take the last non-empty line to be safe.
        last_line = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1]
        payload = json.loads(last_line)
    except Exception:
        return {bt: [] for bt in BET_TYPES}, 0

    results = payload.get("results", {bt: [] for bt in BET_TYPES})
    count   = payload.get("count", 0)
    return results, count
