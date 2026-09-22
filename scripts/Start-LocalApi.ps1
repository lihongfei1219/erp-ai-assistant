param(
    [string]$ReportPath,
    [ValidateRange(1024, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv and reopen PowerShell first.'
}
$launcherPath = Join-Path $PSScriptRoot 'start_backend.py'
$launcherArguments = @($launcherPath, '--port', [string]$Port)
if ($ReportPath) {
    $launcherArguments += @('--report-path', $ReportPath)
}
& uv run --project $projectRoot --cache-dir (Join-Path $projectRoot '.local/uv-cache') --locked python @launcherArguments
if ($LASTEXITCODE -ne 0) {
    throw "Local API exited with code $LASTEXITCODE."
}
