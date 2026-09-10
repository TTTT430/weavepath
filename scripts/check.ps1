[CmdletBinding()]
param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $workspaceRoot "backend"
$webRoot = Join-Path $workspaceRoot "apps\web"
$pluginRoot = Join-Path $workspaceRoot "plugins\weavepath-codex-companion"

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
  & $Python -m compileall -q api agent_runtime graph_core host_adapters tests runtime_events.py ..\integrations
  if ($LASTEXITCODE -ne 0) { throw "Backend compile check failed." }
}
finally {
  Pop-Location
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { throw "Node.js was not found; the Codex companion cannot be verified." }
& $node.Source --check (Join-Path $pluginRoot "server.mjs")
if ($LASTEXITCODE -ne 0) { throw "Codex companion syntax check failed." }
& $node.Source (Join-Path $pluginRoot "tests\smoke.mjs")
if ($LASTEXITCODE -ne 0) { throw "Codex companion loopback smoke test failed." }
$pluginValidator = Join-Path $env:USERPROFILE ".codex\skills\.system\plugin-creator\scripts\validate_plugin.py"
if (Test-Path -LiteralPath $pluginValidator) {
  & $Python $pluginValidator $pluginRoot
  if ($LASTEXITCODE -ne 0) { throw "Codex plugin validation failed." }
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
