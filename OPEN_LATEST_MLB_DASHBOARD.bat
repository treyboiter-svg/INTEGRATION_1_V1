@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PY=python"
for /f "delims=" %%I in ('%PY% -c "from pathlib import Path; p=Path('mlb_daily_outputs'); runs=sorted([x for x in p.iterdir() if x.is_dir()], key=lambda x: x.stat().st_mtime); print(runs[-1] if runs else '')"') do set "LATEST_RUN=%%I"
if not defined LATEST_RUN (
  echo No run directory found in mlb_daily_outputs.
  pause
  exit /b 1
)
if not exist "!LATEST_RUN!\mlb_dashboard_data_bundle.json" %PY% build_dashboard_bundle.py --run-dir "!LATEST_RUN!"
if not exist "!LATEST_RUN!\mlb-pitch-environment-live-dashboard.html" copy /Y "mlb-pitch-environment-live-dashboard.html" "!LATEST_RUN!\mlb-pitch-environment-live-dashboard.html" >nul
start "MLB Dashboard Server" cmd /c "%PY% -m http.server 8765 --directory \"!LATEST_RUN!\""
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/mlb-pitch-environment-live-dashboard.html"
exit /b 0
