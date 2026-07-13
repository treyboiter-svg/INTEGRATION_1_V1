@echo off
title OverlineEdge v8
color 0A
echo =============================================
echo  OverlineEdge Odds Dashboard v8
echo  (No Docker. No compiler. No API keys.)
echo =============================================
echo.

cd /d "%~dp0backend"

echo [Checking Python version...]
python --version 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found!
    echo Install from https://www.python.org/downloads/
    echo Check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo.
echo [Step 1/3] Installing Python dependencies (Python 3.14 compatible)...
python -m pip install -r requirements.txt --prefer-binary
if errorlevel 1 (
    echo.
    echo FAILED: pip install failed.
    echo Try running: python -m pip install --upgrade pip
    pause
    exit /b 1
)

echo.
echo [Step 2/3] Installing Playwright Chromium browser (first run only)...
REM Using "python -m playwright" instead of bare "playwright" because pip
REM installs to a user site-packages Scripts folder that is often NOT on
REM PATH. "python -m" finds the module directly, bypassing PATH entirely.
python -m playwright install chromium
if errorlevel 1 (echo WARN: Playwright browser install issue.)

echo.
echo =============================================
echo  DONE - Server starting now.
echo.
echo  Open in Chrome or Edge:
echo    ..\frontend\odds_dashboard_v8.html
echo.
echo  API Health: http://127.0.0.1:8000/api/health
echo  Export All: http://127.0.0.1:8000/api/export/all
echo =============================================
echo.
echo [Step 3/3] Starting server...
REM Same fix here: "python -m uvicorn" instead of bare "uvicorn".
python -m uvicorn app.main:app --reload --port 8000
pause
