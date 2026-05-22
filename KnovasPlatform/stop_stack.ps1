$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

docker compose --profile mock down
