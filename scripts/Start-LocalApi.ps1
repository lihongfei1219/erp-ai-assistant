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
if (-not $ReportPath) {
    $operatingReport = Join-Path $projectRoot '.local/reports/platform-operating-20260916.json'
    $ReportPath = if (Test-Path -LiteralPath $operatingReport) { $operatingReport } else {
        Join-Path $projectRoot '.local/reports/platform-sales-20260916.json'
    }
}
$resolvedReport = (Resolve-Path -LiteralPath $ReportPath).Path
$localDirectory = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$tokenPath = Join-Path $localDirectory 'api-token.txt'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    $newToken = & $pythonPath -c 'import secrets; print(secrets.token_urlsafe(32))'
    if ($LASTEXITCODE -ne 0) { throw 'Token generation failed.' }
    [System.IO.File]::WriteAllText($tokenPath, $newToken.Trim())
}
$previousToken = $env:ERP_API_TOKEN
$previousReport = $env:ERP_REPORT_PATH
$env:ERP_API_TOKEN = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
$env:ERP_REPORT_PATH = $resolvedReport
Write-Output "Dashboard: http://127.0.0.1:$Port/"
Write-Output "API documentation: http://127.0.0.1:$Port/docs"
Write-Output "Bearer token file (local only): $tokenPath"
Write-Output 'Enter the token on the dashboard. Press Ctrl+C to stop.'
Push-Location (Join-Path $projectRoot 'backend')
try {
    & $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
    $env:ERP_API_TOKEN = $previousToken
    $env:ERP_REPORT_PATH = $previousReport
}
