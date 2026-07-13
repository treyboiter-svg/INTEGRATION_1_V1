# OverlineEdge — Final data-integrity repair (2026-07-10)

## Non-negotiable data rules
1. **No guessed odds.** Moneyline, spread, and total prices are accepted only when the source exposes an explicit signed American price (for example, `-110` or `+105`). A line or total without that source price remains `null` with `source_price_not_exposed` in `data_quality.missing_prices`.
2. **No guessed market-side ownership.** A Polymarket price is assigned to home/away only when team-named outcomes identify both sides, or when a Yes/No question explicitly names exactly one canonical team. Ambiguous Yes/No contracts are rejected as `rejected_ambiguous_outcome_side`.
3. **No false game joins.** Prediction markets require both canonical teams; city/venue data are supporting signals, not substitutes.

## Automatic run diagnostics
Each full cached dashboard refresh writes `backend/data/diagnostics/OverlineEdge_RunDiagnostic_YYYYMMDD_HHMMSS.zip`. It includes a redacted summary, dashboard snapshot, and server log. Fetch metadata at `/api/diagnostics/latest`.

## Source gaps
If ScoresAndOdds does not expose a price in its rendered cell, the system will not invent one. It records the exact book/side/line as missing. This is a transparent upstream-source gap, not an implied value.

## Test
`cd backend; python -m pytest tests -q` — expected: 8 passed.
