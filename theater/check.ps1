$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $checks = @(
        @('python', @('-m', 'compileall', '-q', 'theater/runners', 'theater/src', 'theater/tests')),
        @('python', @('theater/tests/test_sidecars.py')),
        @('python', @('theater/tests/test_project_config.py')),
        @('python', @('theater/tests/test_public_sanitization.py')),
        @('python', @('theater/tests/test_model_canon.py')),
        @('python', @('theater/tests/test_thread.py')),
        @('python', @('theater/tests/test_votes.py')),
        @('python', @('theater/tests/test_update.py')),
        @('python', @('theater/tests/test_mobile.py')),
        @('python', @('theater/tests/test_wordcloud.py')),
        @('python', @('theater/runners/audit_data.py'))
    )
    foreach ($check in $checks) {
        & $check[0] $check[1]
        if ($LASTEXITCODE -ne 0) { throw "Check failed: $($check[0]) $($check[1] -join ' ')" }
    }
    if (Get-Command node -ErrorAction SilentlyContinue) {
        & node --check 'theater/src/webapp/app.js'
        if ($LASTEXITCODE -ne 0) { throw 'JavaScript syntax check failed.' }
    } else {
        Write-Warning 'Node.js is unavailable; skipped app.js syntax check.'
    }
    Write-Host 'ALL CHECKS PASS'
} finally {
    Pop-Location
}
