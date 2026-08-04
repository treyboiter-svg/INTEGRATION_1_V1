@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PY=python"
where %PY% >nul 2>nul
if errorlevel 1 ( echo Python was not found in PATH. & pause & exit /b 1 )
for %%F in (MLB_DAILY_PITCH_ENVIRONMENT_V44.py build_dashboard_bundle_V44.py mlb-pitch-environment-live-dashboard-V44.html V44_IN_APP_GLOSSARY.json mlb_park_reference_full_corrected_v3.csv) do (
  if not exist "%%F" ( echo Missing required file: %%F & pause & exit /b 1 )
)
%PY% MLB_DAILY_PITCH_ENVIRONMENT_V44.py
if errorlevel 1 ( echo V44 run failed. & pause & exit /b 1 )
for /f "delims=" %%I in ('%PY% -c "from pathlib import Path; p=Path('mlb_daily_outputs'); runs=sorted([x for x in p.iterdir() if x.is_dir()], key=lambda x: x.stat().st_mtime); print(runs[-1].resolve() if runs else '')"') do set "RUN=%%I"
if not defined RUN ( echo No run directory found. & pause & exit /b 1 )
%PY% build_dashboard_bundle_V44.py --run-dir "%RUN%"
if errorlevel 1 ( echo Bundle build failed. & pause & exit /b 1 )
copy /Y "mlb-pitch-environment-live-dashboard-V44.html" "%RUN%\mlb-pitch-environment-live-dashboard.html" >nul
start "" "%RUN%\mlb-pitch-environment-live-dashboard.html"
exit /b 0
