param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$uvCache = Join-Path $projectRoot '.local/uv-cache'
$npmCache = Join-Path $projectRoot '.local/npm-cache'

foreach ($command in @('uv', 'node', 'npm.cmd')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Missing prerequisite: $command. Install it and reopen PowerShell."
    }
}

Push-Location $projectRoot
try {
    & uv sync --locked --cache-dir $uvCache
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & uv pip check --python (Join-Path $projectRoot '.venv/Scripts/python.exe') --cache-dir $uvCache
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency compatibility check failed.' }

    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        & npm.cmd ci --cache $npmCache --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    finally { Pop-Location }

    & uv run --locked python (Join-Path $PSScriptRoot 'check_environment.py')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency checks failed.' }
    Write-Host 'Environment ready. Data/configuration notices above may still need attention.'
}
finally { Pop-Location }
