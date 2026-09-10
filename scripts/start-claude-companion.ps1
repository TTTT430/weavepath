[CmdletBinding()]
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
Push-Location $root
try { & $Python -m integrations.claude_code_companion.server }
finally { Pop-Location }
