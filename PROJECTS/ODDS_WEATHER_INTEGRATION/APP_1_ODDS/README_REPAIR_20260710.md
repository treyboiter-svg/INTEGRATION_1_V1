# OverlineEdge v8 — Data-Pipeline Repair (2026-07-10)

## Included repairs
- MLB team sanitizer now removes spaced and fused pitcher suffixes, including multiword pitcher names such as `Mets Mc Lean (R)`.
- Venue authority resolution has a deterministic contained-alias pass for source labels that append pitcher words.
- Scraper retains entire odds-cell text (`raw`) as a fallback when ScoresAndOdds changes a class selector.
- Spread and total parsing extracts a signed 3-digit American price from the complete odds cell, preventing a spread point or total from being misread as a price.
- Kalshi parser preserves title, subtitle, ticker, bid/ask and last trade price.
- Polymarket parser preserves parent event title/slug context, child-market question, outcomes and outcome prices.
- Prediction-market marriage still requires both canonical teams; venue/city is a confidence signal, never a substitute for a team.

## Install
Replace the existing extracted `overlineedge_v8` directory with the `overlineedge_v8` directory contained in this archive. Keep the supplied workbook in the root beside `run.bat` and `run.ps1`.

## Start
Preferred Windows command: `cmd /c run.bat`

If using PowerShell: `Set-ExecutionPolicy -Scope Process Bypass -Force; Unblock-File .\run.ps1; .\run.ps1`

## Validate after start
1. Open `http://127.0.0.1:8000/api/dashboard/mlb`.
2. Confirm MLB `game_count` is nonzero when MLB games are on the slate.
3. Inspect any `per_book.*.spreads.*.american` and `per_book.*.totals.*.american`; signed prices and implied values should populate where the source supplies them.
4. Inspect `match_audit`. Prediction markets attach only when `*_method` is `both_canonical_teams`.
5. Run optional regression suite: `cd backend; python -m pytest tests -q`

## Honest boundary
This repair makes parsers resilient to known markup/data cases and preserves the evidence required for strict joining. A prediction market will remain blank when the exchange has no open contract for that game or its feed does not contain both teams; the app logs this as a rejected market rather than attaching a false match.
