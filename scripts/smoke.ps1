<#
End-to-end smoke test for the ETL Auto-Designer.

Hits the live FastAPI backend on http://localhost:8000:
  1. Creates a project
  2. Uploads test_data/test_source.csv
  3. Generates all ETL scripts
  4. Executes them
  5. Prints per-table status + output files

Prereqs: backend running, OPENAI_API_KEY set (or stubbed prompts).

Usage:
  ./scripts/smoke.ps1
  ./scripts/smoke.ps1 -ApiBase http://localhost:8000
#>

param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$TestSource = "test_data/test_source.csv"
)

$ErrorActionPreference = "Stop"

function Invoke-Api([string]$Method, [string]$Path, $Body = $null, $InFile = $null) {
  $uri = "$ApiBase$Path"
  if ($InFile) {
    return Invoke-RestMethod -Method $Method -Uri $uri -Form @{ file = Get-Item $InFile }
  }
  if ($Body -ne $null) {
    return Invoke-RestMethod -Method $Method -Uri $uri -Body ($Body | ConvertTo-Json -Depth 8) -ContentType "application/json"
  }
  return Invoke-RestMethod -Method $Method -Uri $uri
}

Write-Host "==> Health check $ApiBase"
$health = Invoke-Api "GET" "/api/health" 2>$null
if (-not $health) {
  $health = Invoke-Api "GET" "/health" 2>$null
}
Write-Host "    OK"

Write-Host "==> Create project"
$project = Invoke-Api "POST" "/api/projects/" @{
  name = "smoke-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  description = "Automated smoke test"
}
$projectId = $project.id
Write-Host "    id = $projectId"

Write-Host "==> Upload $TestSource"
if (-not (Test-Path $TestSource)) {
  Write-Error "Test source not found: $TestSource"
  exit 1
}
Invoke-Api "POST" "/api/projects/$projectId/upload-source" -InFile $TestSource | Out-Null
Write-Host "    Uploaded"

Write-Host "==> Generate all tables (this can take a minute)"
$gen = Invoke-Api "POST" "/api/projects/$projectId/generate" @{}
$tables = @($gen.generated_scripts.PSObject.Properties.Name)
Write-Host "    Generated scripts for: $($tables -join ', ')"

Write-Host "==> Execute ETL"
$exec = Invoke-Api "POST" "/api/projects/$projectId/execute"
Write-Host "    overall status = $($exec.status)"

if ($exec.per_table) {
  Write-Host ""
  Write-Host "Per-table results:"
  $exec.per_table | ForEach-Object {
    "{0,-22} {1,-10} rows={2,-8} elapsed={3:N2}s" -f $_.table, $_.status, $_.rows, $_.elapsed
  } | Write-Host
}

Write-Host ""
Write-Host "Output files:"
$exec.output_files | ForEach-Object { "  $_" } | Write-Host

if ($exec.status -ne "success") {
  Write-Host ""
  Write-Host "Last 40 log lines:"
  ($exec.log -split "`n") | Select-Object -Last 40 | ForEach-Object { "  $_" } | Write-Host
  exit 2
}

Write-Host ""
Write-Host "Smoke test PASSED."
