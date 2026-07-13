# OverlineEdge Odds Dashboard v8
### Sports Odds + Prediction Market Edge Finder — Local App, No Docker, No Compiler

---

## ROOT CAUSE OF INSTALL ERRORS (FIXED IN THIS VERSION)

The previous failures were:
- `pandas 2.1.4` and `pandas 2.2.x` both require a **C compiler** on **Python 3.14**
- `numpy 1.26.x` also requires a C compiler on Python 3.14
- You have Python 3.14 (`C:\Python314`) — the newest Python release

### The Fix in v8
- `pandas 3.0.4` — first version with a **pre-built cp314 wheel** (no compiler needed)
- `numpy 2.5.1`  — pre-built cp314 wheel (required by pandas 3.x)
- `--prefer-binary` flag in run.bat — tells pip to ALWAYS use pre-built wheels, never build from source
- All other packages are pure Python — work on any Python version

---

## DO I NEED DOCKER?
**NO. Zero Docker required.**

---

## QUICK START

### Requirements
- Windows 10 or 11
- Python 3.11–3.14 installed from https://www.python.org/downloads/
  - During install, check **"Add Python to PATH"** (required)

### Steps
1. Unzip anywhere (Desktop, C:\apps, etc.)
2. Double-click **`run.bat`**
3. Wait for install (2-3 minutes first time, <5 seconds after)
4. When server says `Uvicorn running on http://127.0.0.1:8000`
5. Open **`frontend/odds_dashboard_v8.html`** in Chrome or Edge
6. Click any game row to expand full per-book breakdown

---

## FILE STRUCTURE

```
overlineedge_v8/
|
+-- run.bat                              <- Windows: double-click to start
+-- run.ps1                              <- PowerShell alternative
+-- README.md                            <- This file
|
+-- frontend/
|   +-- odds_dashboard_v8.html           <- Open in browser (no server needed for this file)
|
+-- backend/
    +-- requirements.txt                 <- Python deps (Python 3.14 compatible)
    +-- app/
        +-- main.py                      <- FastAPI app entry point
        +-- api/
        |   +-- routes.py                <- All API endpoints
        +-- core/
        |   +-- config.py                <- Sport keys, Kalshi/Poly series IDs
        +-- services/
            +-- fetcher.py               <- Orchestrates all data and merging
            +-- odds_scraper.py          <- Playwright headless browser scraper
            +-- normalizer.py            <- Sport-aware team name resolution
            +-- odds_calc.py             <- All math (vig, power method, EV, disparity)
```

---

## HOW IT WORKS

### 1. Bookmaker Scraper
- Playwright launches a headless (invisible) Chrome browser
- Navigates to scoresandodds.com for each sport
- Clicks Moneyline, Spread, and Total tabs and parses each table
- Filters to today's games only
- Covers DraftKings, FanDuel, BetMGM, Caesars, Bovada, BetRivers, etc.
- Max 2 concurrent browsers (prevents memory crashes)

### 2. Kalshi Integration
- Public REST API (no API key needed)
- Pulls open markets filtered by sport series ticker
- Converts yes_bid/yes_ask midpoint to implied probability
- Handles both decimal (0.55) and integer (55) formats

### 3. Polymarket Integration
- Public Gamma API (no API key needed)
- Pulls active events by sport series ID
- Reads outcomePrices array and converts to probability

### 4. Team Name Normalization (normalizer.py)
Old broken approach: static dict with literal newlines causing SyntaxErrors
New approach: tuple-keyed dict (alias_lower, sport) prevents cross-sport collision

Examples:
  - "Panthers" + "nfl"  -> Carolina Panthers (NFL)
  - "Panthers" + "nhl"  -> Florida Panthers (NHL)
  - "Kings"   + "nba"   -> Sacramento Kings
  - "Kings"   + "nhl"   -> Los Angeles Kings
  - "#3 Alabama" + "ncaaf" -> Alabama (rank stripped dynamically)

### 5. Odds Math

| Method | Description |
|--------|-------------|
| Raw Implied % | 1 / decimal_odds * 100 |
| Vig % | (home_raw + away_raw) - 100 |
| Proportional No-Vig | Each side / sum * 100 |
| Power Method No-Vig | Find k where p1^k + p2^k = 1 (Newton-Raphson). Favorite absorbs less vig, underdog more. |
| Power Odds | Average of all books' no-vig power probabilities |
| Disparity % | abs(book_no_vig% - prediction_market%) |
| EV % | (true_prob * (decimal - 1)) - (1 - true_prob), expressed as % |

Power Method Example (-300 / +240 line):
  Raw: Favorite 75.0%, Underdog 29.4% -> Vig 4.4%
  Proportional: 71.8% / 28.2%
  Power: ~72.5% / ~27.5% (matches Pinnacle methodology)

### 6. Consensus Fix
BUG IN V7: raw_implied and no_vig_implied were the same value
FIX IN V8: tracked separately as home_raw_list vs home_nv_list, averaged independently

### 7. Caching and Logging
- Dashboard data cached 120 seconds (TTL uses total_seconds(), not .seconds which caps at 59)
- Every refresh appends a timestamped JSON entry to data/logs/YYYY-MM-DD.jsonl
- CSV export available per-sport or all sports via /api/export/

---

## API ENDPOINTS

| URL | Description |
|-----|-------------|
| GET / | Health check |
| GET /api/health | Cache age + active sports |
| GET /api/dashboard | All sports full data (JSON) |
| GET /api/dashboard/{sport} | Single sport live fetch |
| GET /api/export/all | CSV download - all sports today |
| GET /api/export/{sport} | CSV download - one sport today |
| GET /api/logs/YYYY-MM-DD | View JSON log for any past date |

Valid sport labels: mlb, nfl, nba, nhl, ncaaf, ncaab

---

## DASHBOARD

Click any row to open 6-tab detail panel:

| Tab | Shows |
|-----|-------|
| Moneyline | Per-book: raw%, proportional no-vig%, power no-vig%, vig% |
| Spread | Per-book spread point + American odds + implied% |
| Total O/U | Per-book over/under + implied% |
| Pred Markets | Kalshi + Polymarket home/away%, EV%, volume |
| Disparity | Visual bars for all 4 disparity scores + EV% |
| Power Method | Side-by-side raw vs proportional vs power per book |

Color coding:
  Green badge: disparity >= 3% (potential edge)
  Yellow badge: 1-3% disparity
  Grey badge: < 1% (no edge)

---

## TROUBLESHOOTING

| Problem | Solution |
|---------|----------|
| pandas/numpy compiler error | Fixed — v8 pins to pandas 3.0.4 + numpy 2.5.1 (pre-built cp314 wheels) |
| python not recognized | Install Python 3.11+ from python.org, check Add to PATH |
| Dashboard: Error fetch failed | Backend not running - run run.bat first |
| All sports show 0 games | Off-season is normal (NFL/NBA in July). MLB should show. |
| Kalshi/Poly shows no match | No active market for that game - not a bug |
| Port 8000 already in use | Change --port 8000 to --port 8001 in run.bat, update const API in HTML |
| Scraper returns 0 | Check backend/data/debug_html/{sport}_error.html for raw page |

---

*OverlineEdge v8 - Built for Python 3.14. No Docker. No compiler. No API keys.*
