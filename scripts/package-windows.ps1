[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$SkipTests,
  [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $workspaceRoot "backend"
$webRoot = Join-Path $workspaceRoot "apps\web"
$desktopRoot = Join-Path $workspaceRoot "apps\desktop"
$specPath = Join-Path $workspaceRoot "packaging\weavepath-backend.spec"
$npmCache = Join-Path $workspaceRoot "build\npm-cache"

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
  throw "The NSIS installer can only be built on Windows."
}

if (-not $Python) {
  $venvPython = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) { $Python = $venvPython }
}
if (-not $Python) {
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $pythonCommand) { throw "Python 3.12 was not found." }
  $Python = $pythonCommand.Source
}

if (-not $SkipTests) {
  & (Join-Path $PSScriptRoot "check.ps1") -Python $Python
  if ($LASTEXITCODE -ne 0) { throw "Verification failed before packaging." }
}

Push-Location $webRoot
try {
  if (-not $SkipDependencyInstall) { & npm.cmd ci --cache $npmCache }
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
}
finally { Pop-Location }

if (-not $SkipDependencyInstall) {
  & $Python -m pip install "pyinstaller>=6.15,<7"
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
}
& $Python -m PyInstaller --noconfirm --clean `
  --distpath (Join-Path $workspaceRoot "dist") `
  --workpath (Join-Path $workspaceRoot "build\pyinstaller") `
  $specPath
if ($LASTEXITCODE -ne 0) { throw "Backend sidecar build failed." }

& $Python (Join-Path $workspaceRoot "packaging\create_icon.py") `
  (Join-Path $desktopRoot "build\icon.ico")
if ($LASTEXITCODE -ne 0) { throw "Desktop icon generation failed." }

Push-Location $desktopRoot
try {
  if (-not $SkipDependencyInstall) { & npm.cmd ci --cache $npmCache }
  & npm.cmd run dist:win
  if ($LASTEXITCODE -ne 0) { throw "Windows installer build failed." }
}
finally { Pop-Location }

$installer = Get-ChildItem (Join-Path $workspaceRoot "release\WeavePath-Setup-*.exe") |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $installer) { throw "Packaging completed without producing an installer." }
Write-Host "Windows installer created: $($installer.FullName)" -ForegroundColor Green
