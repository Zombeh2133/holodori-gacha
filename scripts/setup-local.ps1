$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host ""
Write-Host "Holodori Gacha - Local Setup" -ForegroundColor Cyan
Write-Host "============================"
Write-Host ""

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host ".env already exists; leaving it unchanged." -ForegroundColor Yellow
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install/start Docker Desktop first."
}

Write-Host ""
Write-Host "Starting local PostgreSQL..." -ForegroundColor Cyan
docker compose up -d postgres

Write-Host ""
Write-Host "Waiting for PostgreSQL health..." -ForegroundColor Cyan
$maxAttempts = 30
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    $status = docker inspect --format='{{json .State.Health.Status}}' holodori-gacha-postgres 2>$null
    if ($status -match "healthy") {
        Write-Host "PostgreSQL is healthy." -ForegroundColor Green
        break
    }
    if ($attempt -eq $maxAttempts) {
        throw "PostgreSQL did not become healthy in time."
    }
    Start-Sleep -Seconds 2
}

if (-not (Test-Path ".venv")) {
    Write-Host ""
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Write-Host ""
Write-Host "Installing API dependencies..." -ForegroundColor Cyan
& $Python -m pip install --upgrade pip
& $Python -m pip install -r ".\api\requirements.txt"


$AssetToolsPath = Join-Path `
    (Split-Path -Parent $ProjectRoot) `
    "holodori-asset-tools"

if (Test-Path $AssetToolsPath) {
    Write-Host ""
    Write-Host "Installing local holodori-asset-tools..." -ForegroundColor Cyan
    & $Python -m pip install -e $AssetToolsPath
} else {
    Write-Host ""
    Write-Host "holodori-asset-tools was not found next to this project." -ForegroundColor Yellow
    Write-Host "Expected: $AssetToolsPath"
    Write-Host "The API will still run, but real artwork endpoints will stay unavailable."
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Local API, artwork, and importer dependencies are installed." -ForegroundColor Green
Write-Host "For real game imports, also run: .\scripts\setup-local-importer.ps1"
Write-Host "Next run: .\scripts\run-api.ps1"
Write-Host ""
