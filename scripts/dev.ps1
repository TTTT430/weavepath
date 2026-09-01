[CmdletBinding()]
param(
  [string]$Python = "",
  [int]$WebPort = 5173
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $workspaceRoot "backend"
$webRoot = Join-Path $workspaceRoot "apps\web"
$ApiPort = 8000 # Must match the Vite development proxy.

if (-not $Python) {
  $venvPython = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -c "import fastapi, uvicorn" 2>$null
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

if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules"))) {
  throw "Frontend dependencies are missing. Run npm install in apps/web first."
}

$api = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "uvicorn", "api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$ApiPort") `
  -WorkingDirectory $backendRoot -PassThru -WindowStyle Hidden

$web = Start-Process -FilePath "npm.cmd" `
  -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$WebPort") `
  -WorkingDirectory $webRoot -PassThru -WindowStyle Hidden

Write-Host "WeavePath is starting:" -ForegroundColor Cyan
Write-Host "  Web: http://127.0.0.1:$WebPort/"
Write-Host "  API: http://127.0.0.1:$ApiPort/api/v1/health"
Write-Host "Press Ctrl+C to stop both processes."

try {
  while (-not $api.HasExited -and -not $web.HasExited) {
    Start-Sleep -Seconds 1
    $api.Refresh()
    $web.Refresh()
  }
  if ($api.HasExited) { throw "Backend exited with code $($api.ExitCode)." }
  if ($web.HasExited) { throw "Frontend exited with code $($web.ExitCode)." }
}
finally {
  $api.Refresh()
  $web.Refresh()
  if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force }
  if (-not $web.HasExited) { Stop-Process -Id $web.Id -Force }
}
