param(
    [switch]$SkipDeploy,
    [switch]$SkipRestart,
    [switch]$SkipQa
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Write-Stage {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Wait-Http200 {
    param(
        [string]$Uri,
        [int]$Retries = 20,
        [int]$DelayMs = 1000
    )

    for ($i = 0; $i -lt $Retries; $i++) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri $Uri -Method Get -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                return $resp
            }
        } catch {
            # Service may still be restarting.
        }
        Start-Sleep -Milliseconds $DelayMs
    }

    throw "Health check failed for $Uri after $Retries attempts."
}

function Assert-Condition {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw "QA assertion failed: $Message"
    }
}

$deployFiles = @(
    "backend/ai_api.py",
    "backend/ai/reharmonizer.py",
    "frontend/js/api.js",
    "frontend/js/player.js",
    "frontend/js/scale-lab.js",
    "frontend/js/progression-library.js",
    "frontend/js/i18n.js",
    "frontend/css/base.css",
    "frontend/css/home.css",
    "frontend/css/player.css",
    "frontend/player.html",
    "frontend/i18n/en.json",
    "frontend/i18n/zh-TW.json",
    "frontend/admin.html",
    "frontend/benchmark.html",
    "frontend/disclaimer.html",
    "frontend/editor.html",
    "frontend/extraction.html",
    "frontend/help.html",
    "frontend/index.html",
    "frontend/login.html",
    "frontend/privacy.html",
    "frontend/process.html",
    "frontend/tos.html"
)

$summary = [ordered]@{
    Deploy = "skipped"
    Restart = "skipped"
    Qa = "skipped"
}

if (-not $SkipDeploy) {
    Write-Stage "Deploy to V: with SHA256 verification"

    foreach ($rel in $deployFiles) {
        $src = Join-Path $repoRoot $rel
        if (-not (Test-Path $src)) {
            throw "Missing source file: $src"
        }

        if ($rel.StartsWith("backend/")) {
            $dst = Join-Path "V:/backend" ($rel.Substring(8))
        } elseif ($rel.StartsWith("frontend/")) {
            $dst = Join-Path "V:/frontend" ($rel.Substring(9))
        } else {
            throw "Unsupported path mapping for: $rel"
        }

        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path $dstDir)) {
            New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
        }

        Copy-Item -Force $src $dst

        $srcHash = (Get-FileHash -Algorithm SHA256 $src).Hash
        $dstHash = (Get-FileHash -Algorithm SHA256 $dst).Hash
        if ($srcHash -ne $dstHash) {
            throw "Hash verify failed: $rel"
        }

        Write-Host "OK $rel"
    }

    $summary.Deploy = "ok"
}

if (-not $SkipRestart) {
    Write-Stage "Restart NUC service"

    cmd /c "$repoRoot\restart_nuc.bat"

    # restart_nuc.bat may return success even when fallback path is used,
    # so rely on health probe as the source of truth.
    $null = Wait-Http200 -Uri "http://192.168.50.6:8800/" -Retries 30 -DelayMs 1000

    Write-Host "Restart health check: OK"
    $summary.Restart = "ok"
}

if (-not $SkipQa) {
    Write-Stage "Run API QA"

    $homeResp = Wait-Http200 -Uri "http://192.168.50.6:8800/" -Retries 10 -DelayMs 700
    Write-Host ("Home status={0} len={1}" -f $homeResp.StatusCode, $homeResp.Content.Length)

    $body = @{
        chords = @(
            @{ time = 0; end = 4; chord = "C" },
            @{ time = 4; end = 8; chord = "F" },
            @{ time = 8; end = 12; chord = "G7" },
            @{ time = 12; end = 16; chord = "C" }
        )
        key = "C"
        level = 3
        mode = "rule-based"
        strand_flags = @("modal_interchange", "five_alternatives")
    } | ConvertTo-Json -Depth 8

    $resp = Invoke-RestMethod -Method Post -Uri "http://192.168.50.6:8800/api/ai/jazzify" -ContentType "application/json" -Body $body

    $strandFlags = @($resp.strand_flags)
    $notImplemented = @($resp.not_implemented_strands)
    $changes = @($resp.changes)
    $explain = @($resp.explain)

    Assert-Condition ($strandFlags -contains "modal_interchange") "missing modal_interchange in strand_flags"
    Assert-Condition ($strandFlags -contains "five_alternatives") "missing five_alternatives in strand_flags"
    Assert-Condition ($notImplemented.Count -eq 0) "not_implemented_strands should be empty"
    Assert-Condition ($changes.Count -gt 0) "changes should not be empty"
    Assert-Condition ($explain.Count -gt 0) "explain should not be empty"

    Write-Host "Jazzify QA: OK"
    Write-Host ("strand_flags: {0}" -f ($strandFlags -join ","))
    Write-Host ("not_implemented: {0}" -f ($notImplemented -join ","))
    Write-Host ("changes_count: {0}" -f $changes.Count)

    if ($explain.Count -gt 0) {
        $first = $explain[0]
        Write-Host ("explain_first: {0} | {1} -> {2}" -f $first.rule, $first.from, $first.to)
    }

    $summary.Qa = "ok"
}

Write-Stage "Summary"
$summary.GetEnumerator() | ForEach-Object {
    Write-Host ("{0}: {1}" -f $_.Key, $_.Value)
}

Write-Host ""
Write-Host "DONE" -ForegroundColor Green
