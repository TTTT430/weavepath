[CmdletBinding()]
param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $workspaceRoot "backend"
$webRoot = Join-Path $workspaceRoot "apps\web"

if (-not $Python) {
  $venvPython = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -c "import fastapi, pytest" 2>$null
    if ($LASTEXITCODE -eq 0) {
      $Python = $venvPython
    }
  }
}

if (-not $Python) {
  $command = Get-Command python -ErrorAction SilentlyContinue
  if (-not $command) {
    throw "Python was not found. Pass -Python with a Python 3.12 executable."
  }
  $Python = $command.Source
}

Push-Location $backendRoot
try {
  & $Python -m pytest
  if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
  & $Python -m compileall -q api graph_core tests
  if ($LASTEXITCODE -ne 0) { throw "Backend compile check failed." }
}
finally {
  Pop-Location
}

Push-Location $webRoot
try {
  & npm test
  if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }
  & npm run build
  if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
}
finally {
  Pop-Location
}

Write-Host "All backend and frontend checks passed." -ForegroundColor Green
