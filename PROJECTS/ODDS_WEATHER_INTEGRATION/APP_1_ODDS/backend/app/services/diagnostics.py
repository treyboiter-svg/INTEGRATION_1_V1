"""Run-scoped, redacted diagnostic artifacts for OverlineEdge."""
from __future__ import annotations
import json, logging, shutil, sys, zipfile
from datetime import datetime
from pathlib import Path
from app.core.config import LOG_DIR
logger = logging.getLogger(__name__)

def write_run_diagnostic(dashboard: dict) -> str | None:
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = Path("data/diagnostics"); stage = root / f"run_{stamp}"; stage.mkdir(parents=True, exist_ok=True)
        metrics = {}
        for sport, payload in dashboard.items():
            games = payload.get("games", [])
            missing_ml = sum(len(g.get("data_quality", {}).get("missing_prices", {}).get("moneyline", [])) for g in games)
            missing_spread = sum(len(g.get("data_quality", {}).get("missing_prices", {}).get("spread", [])) for g in games)
            missing_total = sum(len(g.get("data_quality", {}).get("missing_prices", {}).get("total", [])) for g in games)
            methods = [g.get("match_audit", {}) for g in games]
            metrics[sport] = {"games": len(games), "missing_moneyline_prices": missing_ml, "missing_spread_prices": missing_spread,
                "missing_total_prices": missing_total,
                "kalshi_matched": sum(not str(x.get("kalshi_method", "")).startswith("rejected") for x in methods),
                "polymarket_matched": sum(not str(x.get("polymarket_method", "")).startswith("rejected") for x in methods)}
        summary = {"run_at": datetime.now().isoformat(), "python": sys.version, "metrics": metrics,
                   "rules": {"odds": "explicit signed source price only; no imputation", "markets": "both canonical teams plus verified price-side ownership"}}
        (stage / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (stage / "dashboard_snapshot.json").write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
        log = Path(LOG_DIR) / "server.log"
        if log.exists(): shutil.copy2(log, stage / "server.log")
        target = root / f"OverlineEdge_RunDiagnostic_{stamp}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for f in stage.rglob("*"):
                if f.is_file(): archive.write(f, f.relative_to(stage))
        shutil.rmtree(stage, ignore_errors=True)
        logger.info("Run diagnostic written: %s", target)
        return str(target)
    except Exception:
        logger.exception("Unable to write run diagnostic")
        return None
