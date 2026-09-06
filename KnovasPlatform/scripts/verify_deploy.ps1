$ErrorActionPreference = "Stop"
# The compose project lives at the repo root, not in KnovasPlatform: the
# per-component stack is gone, and `docker compose` below has to run where
# docker-compose.yml actually is.
$platformDir = (Join-Path $PSScriptRoot "..")
Set-Location (Join-Path $platformDir "..")

# Written by scripts/setup.sh from knovas.env; there is no hand-edited .env.
$platformEnv = (Join-Path $platformDir ".env.generated")
$port = 8081
if (Test-Path $platformEnv) {
    Get-Content $platformEnv | ForEach-Object {
        if ($_ -match '^\s*DOCBRIDGE_WEB_PORT\s*=\s*(.+)\s*$') {
            $port = $Matches[1].Trim()
        }
    }
}

$baseUrl = if ($env:VERIFY_BASE_URL) { $env:VERIFY_BASE_URL } else { "http://localhost:$port" }
$failures = 0

Write-Host "KnovasPlatform deploy verification"
Write-Host "  Base URL: $baseUrl"
Write-Host ""

function Test-Endpoint {
    param([string]$Path, [string]$Desc)
    try {
        Invoke-WebRequest -Uri "$baseUrl$Path" -UseBasicParsing -TimeoutSec 15 | Out-Null
        Write-Host "  OK  $Desc ($Path)"
    } catch {
        Write-Host "  FAIL $Desc ($Path)"
        $script:failures++
    }
}

Test-Endpoint "/health" "nginx liveness"
Test-Endpoint "/api/stats" "app stats"

Write-Host ""
Write-Host "API health JSON:"
try {
    $r = Invoke-WebRequest -Uri "$baseUrl/api/health" -UseBasicParsing -TimeoutSec 15
    Write-Host $r.Content
} catch {
    Write-Host "  FAIL /api/health"
    $failures++
}

if (Test-Path ".\certs") {
    foreach ($f in @("client.crt", "client.key", "ca.crt")) {
        if (Test-Path ".\certs\$f") { Write-Host "  OK  certs\$f present" }
        else { Write-Host "  WARN certs\$f missing" }
    }
} else {
    Write-Host "  WARN .\certs\ not found"
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "Verification failed ($failures check(s))."
    exit 1
}
Write-Host "Verification passed."
