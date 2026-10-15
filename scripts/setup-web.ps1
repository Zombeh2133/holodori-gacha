$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js was not found. Install Node.js 20+ first."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found. Install Node.js 20+ first."
}

if (-not (Test-Path ".\web\.env.local")) {
    Copy-Item ".\web\.env.local.example" ".\web\.env.local"
    Write-Host "Created web\.env.local" -ForegroundColor Green
}

Write-Host ""
Write-Host "Installing Holodori Gacha web dependencies..." -ForegroundColor Cyan
Set-Location ".\web"
npm install
Write-Host ""
Write-Host "Web setup complete." -ForegroundColor Green
Write-Host "Run: .\scripts\run-web.ps1" -ForegroundColor White
