@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PY=python"
where %PY% >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH.
  pause
  exit /b 1
)

for /f "delims=" %%I in ('%PY% -c "from datetime import date; print(date.today().isoformat())"') do set "RUN_DATE=%%I"

echo Running MLB_DAILY_PITCH_ENVIRONMENT_V36.py for !RUN_DATE! ...
%PY% MLB_DAILY_PITCH_ENVIRONMENT_V36.py --date !RUN_DATE!
if errorlevel 1 (
  echo Main run failed.
  pause
  exit /b 1
)

for /f "delims=" %%I in ('%PY% -c "from pathlib import Path; p=Path('mlb_daily_outputs'); runs=sorted([x for x in p.iterdir() if x.is_dir()], key=lambda x: x.stat().st_mtime); print(runs[-1] if runs else '')"') do set "LATEST_RUN=%%I"
if not defined LATEST_RUN (
  echo Could not locate latest run directory.
  pause
  exit /b 1
)

echo Building dashboard bundle in !LATEST_RUN! ...
%PY% build_dashboard_bundle_V36.py --run-dir "!LATEST_RUN!"
if errorlevel 1 (
  echo Bundle build failed.
  pause
  exit /b 1
)

copy /Y "mlb-pitch-environment-live-dashboard.html" "!LATEST_RUN!\mlb-pitch-environment-live-dashboard.html" >nul

echo Starting local server...
start "MLB Dashboard Server" cmd /c "%PY% -m http.server 8765 --directory \"!LATEST_RUN!\""
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/mlb-pitch-environment-live-dashboard.html"

echo Dashboard opened for !LATEST_RUN!
exit /b 0
