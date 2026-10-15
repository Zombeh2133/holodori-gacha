$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $ProjectRoot "web")

if (-not (Test-Path ".\node_modules")) {
    throw "Web dependencies are missing. Run .\scripts\setup-web.ps1 first."
}

Write-Host ""
Write-Host "Starting Holodori Gacha website..." -ForegroundColor Cyan
Write-Host "http://127.0.0.1:3000" -ForegroundColor Green
Write-Host ""

npm run dev -- --hostname 127.0.0.1 --port 3000
