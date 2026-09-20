param(
    [switch]$Apply,
    [switch]$IncludeDownloadCaches
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$workspacePrefix = $workspaceRoot + '\'
$targets = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function Assert-SafeTarget([string]$Path, [switch]$SkipGitCheck) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not $resolved.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside workspace: $resolved"
    }
    $relative = $resolved.Substring($workspacePrefix.Length)
    if ($relative -match '^(\.git|\.venv|database_backups)(\\|$)' -or
        $relative -match '^frontend\\(node_modules|dist)(\\|$)' -or
        $relative -match '^\.local\\(reports|feishu-app|discovery|maintenance)(\\|$)' -or
        $relative -match '^\.env($|\.)|^\.ssh_known_hosts$' -or
        $relative -match '^\.local\\(api-token\.txt|feishu-app\.json|feishu-webhook\.txt|feishu-secret\.txt)$' -or
        $relative -match '^\.local\\[^\\]+\.(log|pid)$') {
        throw "Refusing protected path: $relative"
    }
    $cursor = Get-Item -Force -LiteralPath $resolved
    while ($cursor.FullName -ne $workspaceRoot) {
        if ($null -eq $cursor) { throw "Unable to verify ancestor: $resolved" }
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing reparse point: $($cursor.FullName)"
        }
        $cursor = if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory }
    }
    if (-not $SkipGitCheck) {
        $tracked = @(git -C $workspaceRoot ls-files -- $relative.Replace('\', '/'))
        if ($LASTEXITCODE -ne 0) { throw 'Unable to verify Git tracking.' }
        if ($tracked.Count) { throw "Refusing tracked files: $relative" }
    }
    return $resolved
}

function Add-Candidate([string]$Relative) {
    $path = Join-Path $workspaceRoot $Relative
    if (Test-Path -LiteralPath $path) {
        [void]$targets.Add((Assert-SafeTarget $path))
    }
}

function Get-SafeFiles([string]$Path) {
    $item = Get-Item -Force -LiteralPath $Path
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point: $Path" }
    if (-not $item.PSIsContainer) { return $item }
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($Path)
    while ($pending.Count) {
        foreach ($child in Get-ChildItem -Force -LiteralPath $pending.Pop()) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing nested reparse point: $($child.FullName)"
            }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) } else { $child }
        }
    }
}

function Remove-SafeCandidate([string]$Path, $Progress) {
    $files = @(Get-SafeFiles $Path)
    foreach ($file in $files) {
        try {
            $null = Assert-SafeTarget $file.FullName -SkipGitCheck
            $size = $file.Length
            Remove-Item -LiteralPath $file.FullName -Force
            $Progress.RemovedFiles++
            $Progress.RemovedBytes += $size
        }
        catch {
            if ($_.CategoryInfo.Category -notin @('PermissionDenied', 'WriteError') -and
                $_.Exception -isnot [UnauthorizedAccessException] -and
                $_.Exception -isnot [IO.IOException]) { throw }
            $Progress.Busy += $file.FullName.Substring($workspacePrefix.Length)
        }
    }
    if ((Test-Path -LiteralPath $Path -PathType Container)) {
        # Only remove verified empty directories; never force through a locked DLL.
        $directories = [System.Collections.Generic.List[string]]::new()
        $pending = [System.Collections.Generic.Stack[string]]::new()
        $pending.Push($Path)
        while ($pending.Count) {
            $current = $pending.Pop()
            try {
                $null = Assert-SafeTarget $current -SkipGitCheck
                [void]$directories.Add($current)
                foreach ($child in Get-ChildItem -Force -Directory -LiteralPath $current) {
                    # Assert before any descent; a junction is never enumerated.
                    $null = Assert-SafeTarget $child.FullName -SkipGitCheck
                    $pending.Push($child.FullName)
                }
            }
            catch {
                if ($_.CategoryInfo.Category -ne 'PermissionDenied' -and
                    $_.Exception -isnot [UnauthorizedAccessException]) { throw }
                $Progress.Busy += $current.Substring($workspacePrefix.Length)
            }
        }
        foreach ($directory in ($directories | Sort-Object Length -Descending)) {
            try {
                $null = Assert-SafeTarget $directory -SkipGitCheck
                if (@(Get-ChildItem -Force -LiteralPath $directory).Count -eq 0) {
                    Remove-Item -LiteralPath $directory -Force
                }
            }
            catch {
                if ($_.CategoryInfo.Category -notin @('PermissionDenied', 'WriteError') -and
                    $_.Exception -isnot [UnauthorizedAccessException] -and
                    $_.Exception -isnot [IO.IOException]) { throw }
                $Progress.Busy += $directory.Substring($workspacePrefix.Length)
            }
        }
    }
}

foreach ($relative in @('.ruff_cache', 'backend/.ruff_cache', 'backend/.pytest_cache',
                         'frontend/test-results', 'frontend/playwright-report')) {
    Add-Candidate $relative
}
# Traverse only first-party Python trees, never dependency environments.
foreach ($relative in @('backend/app', 'backend/tests', 'backend/workers', 'scripts')) {
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push((Join-Path $workspaceRoot $relative))
    while ($pending.Count) {
        foreach ($directory in Get-ChildItem -Force -Directory -LiteralPath $pending.Pop()) {
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($directory.Name -eq '__pycache__') {
                Add-Candidate $directory.FullName.Substring($workspacePrefix.Length)
            } else { $pending.Push($directory.FullName) }
        }
    }
}

$localRoot = Join-Path $workspaceRoot '.local'
if ((Get-Item -Force -LiteralPath $localRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Refusing .local reparse point.'
}
foreach ($directory in Get-ChildItem -Force -Directory -LiteralPath $localRoot) {
    if ($directory.Name -match '^(pytest($|-)|test-tmp-|familiarization-pytest-|phase2-(backend|rules|test)-)' -or
        $directory.Name -match '^feishu-(app-(full-tests|tests-\d+)|card-(full|green|verified)|dependency-tests|final-tests|full-tests-\d+|model-green|phase-a-(full|final)|sales-green|schedule-tests|tests-initial)$' -or
        $directory.Name -in @('feishu-display', 'playwright-results')) {
        Add-Candidate ('.local/' + $directory.Name)
    }
}

# Known one-off artifacts; no blanket extension deletion in .local.
$patterns = @('analytics-layout-*.png', 'analytics-layout-*.html', 'feishu-doc-*.txt')
foreach ($pattern in $patterns) {
    foreach ($file in Get-ChildItem -File -LiteralPath $localRoot -Filter $pattern) {
        Add-Candidate ('.local/' + $file.Name)
    }
}
$artifacts = @(
    'analysis-dashboard.png', 'analysis-mobile.png', 'analysis-natural-language.png',
    'phase2-dashboard.png', 'phase2-mobile.png', 'phase2-real-dashboard.png',
    'feishu-preview-desktop.png', 'feishu-preview-mobile.png', 'feishu-real-demo.png',
    'feishu-demo-20260916.json', 'feishu-sales-ranking-preview.txt',
    'feishu-ranking-card.json', 'feishu-ranking-card-preview.html',
    'feishu-ranking-card-desktop.png', 'feishu-ranking-card-mobile.png',
    'feishu-summary-card.json', 'feishu-summary-card-preview.html',
    'feishu-summary-card-desktop.png', 'feishu-summary-card-mobile.png',
    'feishu-trend-card.json', 'feishu-trend-card-preview.html',
    'feishu-trend-card-desktop.png', 'feishu-trend-card-mobile.png',
    'build-feishu-card-preview.py', 'capture-feishu-analytics-layout.cjs',
    'check-feishu-idle.py', 'check-feishu-ui.cjs', 'check-operating-ui.cjs',
    'diagnose-feishu-reply.py', 'fetch-feishu-preview-assets.cjs',
    'preview-feishu-analytics-layout.py', 'preview-feishu-card.cjs',
    'preview-summary.cjs', 'preview-trend.cjs', 'read-feishu-display-docs.cjs',
    'smoke_api.py', 'validate-sales-query.py', 'vchart-1.12.3.min.js'
)
foreach ($artifact in $artifacts) { Add-Candidate ('.local/' + $artifact) }
if ($IncludeDownloadCaches) {
    Add-Candidate '.local/npm-cache'
    Add-Candidate '.local/uv-cache'
}

$skipped = @()
$plan = @(foreach ($target in ($targets | Sort-Object)) {
    try { $files = @(Get-SafeFiles $target) }
    catch {
        if ($_.CategoryInfo.Category -ne 'PermissionDenied' -and
            $_.Exception -isnot [UnauthorizedAccessException]) { throw }
        $relative = $target.Substring($workspacePrefix.Length)
        $skipped += [pscustomobject]@{ Path = $relative; Reason = 'Access denied during inspection' }
        Write-Warning "Skipped unreadable candidate: $relative"
        continue
    }
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    [pscustomobject]@{
        Path = $target.Substring($workspacePrefix.Length)
        Files = $files.Count
        Bytes = [long]$bytes
    }
})
$plan | Select-Object Path, Files, @{Name='MiB'; Expression={[math]::Round($_.Bytes / 1MB, 2)}} | Format-Table -AutoSize
$totalFiles = [long]($plan | Measure-Object -Property Files -Sum).Sum
$totalBytes = [long]($plan | Measure-Object -Property Bytes -Sum).Sum
Write-Output ("Candidates: {0}; files: {1}; logical MiB: {2:N2}" -f $plan.Count, $totalFiles, ($totalBytes / 1MB))
if (-not $Apply) { Write-Output 'Preview only. Add -Apply to delete these candidates.'; exit 0 }

$reportDirectory = Join-Path $workspaceRoot '.local/maintenance'
if (-not (Test-Path -LiteralPath $reportDirectory)) {
    New-Item -ItemType Directory -Path $reportDirectory | Out-Null
}
if ((Get-Item -Force -LiteralPath $reportDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Refusing report directory reparse point.'
}
$reportPath = Join-Path $reportDirectory 'cleanup-last.json'
if ((Test-Path -LiteralPath $reportPath) -and
    ((Get-Item -Force -LiteralPath $reportPath).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Refusing report file reparse point.'
}
$report = [ordered]@{
    StartedAt = (Get-Date).ToString('o'); Completed = $false
    Targets = $plan; Deleted = @(); Skipped = $skipped
    RemovedFiles = 0; LogicalBytesRemoved = [long]0
}
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
foreach ($item in $plan) {
    $target = Assert-SafeTarget (Join-Path $workspaceRoot $item.Path)
    $null = @(Get-SafeFiles $target)
    $result = [pscustomobject]@{ RemovedFiles = 0; RemovedBytes = [long]0; Busy = @() }
    try { Remove-SafeCandidate -Path $target -Progress $result }
    catch {
        $report.Failure = [pscustomobject]@{ Path = $item.Path; Reason = $_.Exception.GetType().Name }
        throw
    }
    finally {
        $report.RemovedFiles += $result.RemovedFiles
        $report.LogicalBytesRemoved += $result.RemovedBytes
        if (-not (Test-Path -LiteralPath $target)) { $report.Deleted += $item.Path }
        foreach ($busy in $result.Busy) {
            $report.Skipped += [pscustomobject]@{ Path = $busy; Reason = 'In use or access denied; kept' }
        }
        $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    }
}
$report.Completed = $true
$report.CompletedAt = (Get-Date).ToString('o')
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Output 'Cleanup complete. Report: .local/maintenance/cleanup-last.json'
