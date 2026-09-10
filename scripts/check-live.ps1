[CmdletBinding()]
param([string]$Python = "", [string]$Node = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
if (-not $Node) {
  $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
  if (-not $nodeCommand) { throw "Node.js was not found." }
  $Node = $nodeCommand.Source
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $root "backend"
try {
  Push-Location $root
  try {
    & $Python "scripts\p4-live-recovery-smoke.py"
    if ($LASTEXITCODE -ne 0) { throw "P4 live model recovery failed." }
    & $Python -m integrations.claude_code_companion.live_readonly
    if ($LASTEXITCODE -ne 0) { throw "Claude Code live companion check failed." }
    & $Node "plugins\weavepath-codex-companion\tests\live-readonly.mjs"
    if ($LASTEXITCODE -ne 0) { throw "Codex live companion check failed." }
  }
  finally { Pop-Location }
}
finally { $env:PYTHONPATH = $previousPythonPath }

Write-Host "All P4 live connection and recovery checks passed." -ForegroundColor Green
