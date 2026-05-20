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

docker compose up -d docbridge-web docbridge-web-nginx
docker compose ps
