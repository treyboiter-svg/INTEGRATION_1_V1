[CmdletBinding()]
param(
  [string]$AppRoot = (Get-Location).Path,
  [string]$ApiBase = "http://127.0.0.1:8000",
  [switch]$RunTests,
  [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$root = (Resolve-Path $AppRoot).Path
$work = Join-Path $root "diagnostic_staging_$stamp"
$outZip = Join-Path $root "OverlineEdge_Diagnostics_$stamp.zip"
New-Item -ItemType Directory -Force -Path $work | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $work "api"), (Join-Path $work "logs"), (Join-Path $work "source"), (Join-Path $work "system") | Out-Null

function Save-Text([string]$Path, [string]$Text) { $Text | Out-File -FilePath $Path -Encoding utf8 -Width 5000 }
function Add-CommandOutput([string]$Name, [scriptblock]$Command) {
  try { & $Command 2>&1 | Out-String -Width 5000 | Out-File (Join-Path $work "system\$Name.txt") -Encoding utf8 }
  catch { Save-Text (Join-Path $work "system\$Name.txt") ("COMMAND FAILURE`r`n" + ($_ | Out-String)) }
}
function Get-ApiSnapshot([string]$Name, [string]$Uri) {
  try {
    $r = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec $TimeoutSeconds
    $r.Content | Out-File (Join-Path $work "api\$Name.json") -Encoding utf8
    return @{ endpoint=$Uri; status=$r.StatusCode; bytes=$r.Content.Length; error=$null }
  } catch {
    Save-Text (Join-Path $work "api\$Name.ERROR.txt") ($_ | Out-String)
    return @{ endpoint=$Uri; status=$null; bytes=0; error=$_.Exception.Message }
  }
}

$summary = [ordered]@{
  collected_at = (Get-Date).ToString("o")
  app_root = $root
  api_base = $ApiBase
  script_version = "2026-07-10.1"
  api_checks = @()
  workbook = $null
  files_collected = @()
  test_requested = [bool]$RunTests
}

# API snapshots: these do not alter production data; they expose the current state.
$summary.api_checks += Get-ApiSnapshot "health" "$ApiBase/api/health"
foreach ($sport in @("mlb","nfl","nba","nhl","ncaaf","ncaab")) {
  $summary.api_checks += Get-ApiSnapshot "dashboard_$sport" "$ApiBase/api/dashboard/$sport"
}

# Workbook identity only: never put credentials or environment files in a support bundle.
$wb = Get-ChildItem -Path $root -Filter "US_SPORTS_VENUES_MASTER_CORRECTED_V2.xlsx" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($wb) {
  $summary.workbook = [ordered]@{ path=$wb.FullName; bytes=$wb.Length; sha256=(Get-FileHash $wb.FullName -Algorithm SHA256).Hash; modified_utc=$wb.LastWriteTimeUtc.ToString("o") }
  Copy-Item $wb.FullName (Join-Path $work $wb.Name) -Force
}

# Capture relevant sources and run artifacts, excluding secrets, environments, caches, and browser binaries.
$include = @(
  "backend\app\services\fetcher.py", "backend\app\services\_scrape_worker.py",
  "backend\app\services\venue_resolver.py", "backend\app\services\odds_scraper.py",
  "backend\app\services\normalizer.py", "backend\app\core\config.py",
  "backend\app\api\routes.py", "backend\app\main.py", "backend\requirements.txt",
  "README_REPAIR_20260710.md", "README.md", "run.bat", "run.ps1"
)
foreach ($relative in $include) {
  $source = Join-Path $root $relative
  if (Test-Path $source) {
    $target = Join-Path $work ("source\" + $relative)
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item $source $target -Force
    $summary.files_collected += $relative
  }
}
foreach ($dir in @("backend\data\logs", "backend\data\debug_html")) {
  $sourceDir = Join-Path $root $dir
  if (Test-Path $sourceDir) {
    Get-ChildItem $sourceDir -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 20 | ForEach-Object {
      $target = Join-Path $work ("logs\" + $_.Name)
      Copy-Item $_.FullName $target -Force
    }
  }
}

Add-CommandOutput "powershell_version" { $PSVersionTable | Format-List }
Add-CommandOutput "python_version" { python --version }
Add-CommandOutput "python_packages" { python -m pip freeze }
Add-CommandOutput "processes_port_8000" { Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Format-List * }
Add-CommandOutput "root_listing" { Get-ChildItem $root -Force | Select-Object Name,Length,LastWriteTimeUtc | Format-Table -AutoSize }

if ($RunTests) {
  $backend = Join-Path $root "backend"
  if (Test-Path $backend) {
    Push-Location $backend
    Add-CommandOutput "pytest" { python -m pytest tests -q }
    Pop-Location
  }
}

# Parse live snapshots into concise health metrics without modifying the API output.
$metrics = [ordered]@{}
Get-ChildItem (Join-Path $work "api") -Filter "dashboard_*.json" | ForEach-Object {
  try {
    $data = Get-Content $_.FullName -Raw | ConvertFrom-Json
    $payload = if ($data.Content -is [string]) { $data.Content | ConvertFrom-Json } else { $data }
    $games = @($payload.games)
    $spreadNull = 0; $spreadSeen = 0; $totalNull = 0; $totalSeen = 0; $kalMatched = 0; $polyMatched = 0
    foreach ($game in $games) {
      if ($game.match_audit.kalshi_method -and $game.match_audit.kalshi_method -notlike "rejected*") { $kalMatched++ }
      if ($game.match_audit.polymarket_method -and $game.match_audit.polymarket_method -notlike "rejected*") { $polyMatched++ }
      foreach ($book in $game.per_book.psobject.Properties.Value) {
        foreach ($v in $book.spreads.psobject.Properties.Value) { $spreadSeen++; if ($null -eq $v.american) { $spreadNull++ } }
        foreach ($v in $book.totals.psobject.Properties.Value) { $totalSeen++; if ($null -eq $v.american) { $totalNull++ } }
      }
    }
    $metrics[$_.BaseName] = [ordered]@{ games=$games.Count; spread_prices_seen=$spreadSeen; spread_prices_null=$spreadNull; total_prices_seen=$totalSeen; total_prices_null=$totalNull; kalshi_matches=$kalMatched; polymarket_matches=$polyMatched }
  } catch { $metrics[$_.BaseName] = @{ parse_error=$_.Exception.Message } }
}
$summary["dashboard_metrics"] = $metrics
$summary | ConvertTo-Json -Depth 12 | Out-File (Join-Path $work "summary.json") -Encoding utf8

if (Test-Path $outZip) { Remove-Item $outZip -Force }
Compress-Archive -Path (Join-Path $work "*") -DestinationPath $outZip -CompressionLevel Optimal
Remove-Item $work -Recurse -Force
Write-Host ""
Write-Host "Diagnostics created:" -ForegroundColor Green
Write-Host $outZip -ForegroundColor Cyan
Write-Host "Upload that ZIP here. It excludes .env files and other credential-bearing files." -ForegroundColor Yellow
