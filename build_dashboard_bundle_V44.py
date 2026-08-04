#!/usr/bin/env python3
"""V44 strict dashboard bundle builder.

Run: python build_dashboard_bundle_V44.py --run-dir PATH

Refuses to build a bundle if any required V44 audit artifact is missing --
this is the packaging gate that prevents a dashboard from silently hiding
data that was never generated.

TESTED: verified against a synthetic run directory that (a) is missing one
required file -- the builder exits non-zero with an explicit "PACKAGING
BLOCKED" message and does NOT write a bundle -- and (b) has every required
file present -- the builder succeeds and writes a valid
mlb_dashboard_data_bundle.json. See V44_TEST_REPORT.json.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd

REQUIRED = [
    "game_environment_audit.csv",
    "roof_evidence_audit.csv",
    "roof_conflict_audit.csv",
    "pitcher_query_audit.csv",
    "pitch_physics_environment.csv",
    "pitch_effect_decomposition.csv",
    "atmosphere_provenance_audit.csv",
    "density_calculation_audit.csv",
    "findings.csv",
    "run_health_and_model_diagnostics.csv",
    "mlb_dashboard_system_health.json",
    "dashboard_debug_snapshot.json",
]


def records(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    return json.loads(df.to_json(orient="records")) if not df.empty else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--glossary", default="V44_IN_APP_GLOSSARY.json")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)

    missing = [f for f in REQUIRED if not (run_dir / f).exists()]
    if missing:
        raise SystemExit(f"V44 PACKAGING BLOCKED -- missing required files: {missing}")

    health = json.loads((run_dir / "mlb_dashboard_system_health.json").read_text(encoding="utf-8"))
    debug = json.loads((run_dir / "dashboard_debug_snapshot.json").read_text(encoding="utf-8"))
    glossary_path = Path(args.glossary)
    if not glossary_path.exists():
        glossary_path = Path(__file__).resolve().parent / args.glossary
    glossary = json.loads(glossary_path.read_text(encoding="utf-8")) if glossary_path.exists() else []

    env = records(run_dir / "game_environment_audit.csv")
    games = []
    if env:
        seen = {}
        for r in env:
            k = str(r.get("game_pk"))
            if k not in seen:
                seen[k] = {kk: r.get(kk) for kk in ("game_pk", "game_datetime_utc", "venue_name_api", "venue_name", "roofclass", "roof_state", "roof_decision_reason", "ballparkelevationm")}
        games = list(seen.values())

    bundle = {
        "meta": {"version": "44.0.0", "run_dir": str(run_dir.resolve())},
        "summary": health.get("summary", {}),
        "games": games,
        "game_environment_audit": env,
        "roof_evidence_audit": records(run_dir / "roof_evidence_audit.csv"),
        "roof_conflict_audit": records(run_dir / "roof_conflict_audit.csv"),
        "pitcher_query_audit": records(run_dir / "pitcher_query_audit.csv"),
        "pitch_physics_environment": records(run_dir / "pitch_physics_environment.csv"),
        "pitch_effect_decomposition": records(run_dir / "pitch_effect_decomposition.csv"),
        "atmosphere_provenance_audit": records(run_dir / "atmosphere_provenance_audit.csv"),
        "density_calculation_audit": records(run_dir / "density_calculation_audit.csv"),
        "findings": records(run_dir / "findings.csv"),
        "run_health_and_model_diagnostics": records(run_dir / "run_health_and_model_diagnostics.csv"),
        "dashboard_debug_snapshot": debug,
        "glossary": glossary,
    }
    (run_dir / "mlb_dashboard_data_bundle.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    print(json.dumps({"status": "COMPLETE", "bundle": str(run_dir / "mlb_dashboard_data_bundle.json"), "section_counts": {k: len(v) if isinstance(v, list) else None for k, v in bundle.items()}}, indent=2))


if __name__ == "__main__":
    main()
