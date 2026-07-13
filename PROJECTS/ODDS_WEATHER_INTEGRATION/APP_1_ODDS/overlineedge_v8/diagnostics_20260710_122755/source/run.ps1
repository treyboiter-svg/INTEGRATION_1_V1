Write-Host '=== OverlineEdge v8 ===' -ForegroundColor Cyan
Write-Host 'No Docker. No compiler. No API keys.' -ForegroundColor Green

# CRITICAL: must cd into backend/ BEFORE starting uvicorn, or Python
# cannot find the 'app' module (ModuleNotFoundError: No module named 'app').
Set-Location "$PSScriptRoot\backend"

Write-Host '[1/3] Installing deps (Python 3.14 compatible)...' -ForegroundColor Yellow
python -m pip install -r requirements.txt --prefer-binary

Write-Host '[2/3] Installing Playwright browser...' -ForegroundColor Yellow
# "python -m playwright" avoids relying on the Scripts folder being on PATH,
# which is often not the case with pip's --user installs on Windows.
python -m playwright install chromium

Write-Host ''
Write-Host 'Open: ..\frontend\odds_dashboard_v8.html' -ForegroundColor Cyan
Write-Host '[3/3] Starting backend...' -ForegroundColor Green
python -m uvicorn app.main:app --reload --port 8000
