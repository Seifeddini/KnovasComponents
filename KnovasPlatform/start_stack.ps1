$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Update it, then re-run."
    exit 1
}

if (-not (Test-Path "components/docbridge_integration")) {
    Write-Host "Missing components/docbridge_integration."
    exit 1
}

# Full rebuild so the running UI matches this repo (avoids stale labels from cached layers).
Write-Host "Building docbridge-web (no cache)..."
docker compose build --no-cache docbridge-web

Write-Host "Starting docbridge-web and docbridge-web-nginx..."
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
docker compose ps
