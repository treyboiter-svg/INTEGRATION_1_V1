# OverlineEdge v8 — Venue-Authority Matching Patch

## What changed
This bundle replaces title-only sportsbook / Kalshi / Polymarket matching with a deterministic venue-authority resolver backed by `US_SPORTS_VENUES_MASTER_CORRECTED_V2.xlsx`.

### Matching invariants
1. A sportsbook row is rejected unless both teams resolve to the supplied workbook in the requested league.
2. Team aliases are league-scoped; unique mascot aliases and compound mascots (e.g. Red Sox / White Sox) resolve deterministically.
3. MLB pitcher suffixes and fused labels are cleaned before identity resolution: `RedSoxSandoval(L)` -> `Boston Red Sox`.
4. A Kalshi or Polymarket market is accepted only where **both** canonical teams appear in the candidate title. A city or venue can strengthen a match but can never replace a missing team.
5. All accepted games expose home venue/city/state/coordinates/elevation/orientation and a `match_audit` record in `/api/dashboard`.
6. Spread and total parsers now combine line and price fields before implied-probability calculation.

## Launch
Run `run.ps1` or `run.bat` from this folder. The workbook must remain at this bundle root.

## Verify
After server startup:
`http://127.0.0.1:8000/api/dashboard/mlb`

For every game, verify:
- `venue` is present and matches the authoritative home team.
- `match_audit.away_score` and `match_audit.home_score` are 1.0 for direct canonical matches.
- A prediction market is populated only when `match_audit.<market>_method` is `both_canonical_teams`.

No market title with only one team is allowed to attach to a game.
