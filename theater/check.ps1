$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $checks = @(
        @('python', @('-m', 'compileall', '-q', 'theater/runners', 'theater/src', 'theater/tests')),
        @('python', @('theater/tests/test_sidecars.py')),
        @('python', @('theater/tests/test_books.py')),
        @('python', @('theater/tests/test_book_order.py')),
        @('python', @('theater/tests/test_book_document.py')),
        @('python', @('theater/tests/test_book_pdf.py')),
        @('python', @('theater/tests/test_project_config.py')),
        @('python', @('theater/tests/test_model_canon.py')),
        @('python', @('theater/tests/test_collect_safety.py')),
        @('python', @('theater/tests/test_thread.py')),
        @('python', @('theater/tests/test_votes.py')),
        @('python', @('theater/tests/test_update.py')),
        @('python', @('theater/tests/test_mobile.py')),
        @('python', @('theater/tests/test_mobile_pages.py')),
        @('python', @('theater/tests/test_wordcloud.py')),
        @('python', @('theater/tests/test_release_candidate.py')),
        @('python', @('theater/release/check_candidate.py')),
        @('python', @('theater/runners/audit_data.py'))
    )
    foreach ($check in $checks) {
        & $check[0] $check[1]
        if ($LASTEXITCODE -ne 0) { throw "Check failed: $($check[0]) $($check[1] -join ' ')" }
    }
    # Only the public checkout contains this test. Keep private development
    # checks small, but require current-tree and reachable-history privacy checks
    # when the public release is actually validated.
    if (Test-Path -LiteralPath 'theater/tests/test_public_sanitization.py') {
        & python theater/tests/test_public_sanitization.py
        if ($LASTEXITCODE -ne 0) { throw 'Public sanitization check failed' }
    }
    if (Get-Command node -ErrorAction SilentlyContinue) {
        foreach ($script in @('theater/src/webapp/app.js', 'theater/src/webapp/sw.js', 'theater/src/webapp/mobile-sw.js')) {
            & node --check $script
            if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $script" }
        }
    } else {
        Write-Warning 'Node.js is unavailable; skipped app.js syntax check.'
    }
    Write-Host 'ALL CHECKS PASS'
} finally {
    Pop-Location
}
