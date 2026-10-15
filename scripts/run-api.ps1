$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    throw ".env is missing. Run .\scripts\setup-local.ps1 first."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw ".venv is missing. Run .\scripts\setup-local.ps1 first."
}

Write-Host ""
Write-Host "Starting Holodori Gacha API..." -ForegroundColor Cyan
Write-Host "Swagger: http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host ""

& ".\.venv\Scripts\python.exe" -m uvicorn `
    app.main:app `
    --app-dir ".\api" `
    --host 127.0.0.1 `
    --port 8000 `
    --reload
