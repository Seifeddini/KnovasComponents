$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

docker compose --profile sync down
