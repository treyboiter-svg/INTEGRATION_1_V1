# Verified odds-evidence pipeline

The scraper preserves every rendered sportsbook cell's visible text, HTML attributes, and descendant-node semantic metadata. Moneyline, Spread, and Total are resolved separately.

## Deterministic recovery sequence
1. Retain DOM cell evidence: rendered text, HTML, classes, `data-*`, ARIA/title attributes, and child nodes.
2. Extract the sportsbook price from semantic price fields (`moneyline`, `american`, `odds`, `price`) before using cell-text fallback.
3. Extract spread/total lines independently from line/value context.
4. Use signed cell text only when structured markup is unavailable.
5. Reject only missing, contradictory, or unprovable candidates; return the exact parse reason and DOM-evidence method.
6. Record moneyline/spread/total gaps by game, book, side, line, parse reason, and evidence in diagnostics.

The application never manufactures a number. It does recover the source's actual value from structured DOM context and independently validates line and price before publishing it. Automatic per-refresh ZIP diagnostics include the snapshot and parse-quality totals.

Run tests: `cd backend; python -m pytest tests -q` — expected: 9 passed.
