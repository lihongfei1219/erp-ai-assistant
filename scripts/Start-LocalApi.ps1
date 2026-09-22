param(
    [string]$ReportPath,
    [ValidateRange(1024, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Initialize the project .venv and install backend/requirements.lock first.'
}
$launcherPath = Join-Path $PSScriptRoot 'start_backend.py'
$launcherArguments = @($launcherPath, '--port', [string]$Port)
if ($ReportPath) {
    $launcherArguments += @('--report-path', $ReportPath)
}
& $pythonPath @launcherArguments
if ($LASTEXITCODE -ne 0) {
    throw "Local API exited with code $LASTEXITCODE."
}
