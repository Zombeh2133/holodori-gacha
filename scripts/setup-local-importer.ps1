$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Example = Join-Path `
    $ProjectRoot `
    ".env.local-importer.example"

$Target = Join-Path `
    $ProjectRoot `
    ".env.local-importer"

if (-not (Test-Path $Target)) {
    Copy-Item $Example $Target
    Write-Host ""
    Write-Host "Created .env.local-importer" -ForegroundColor Green
    Write-Host "Fill in HD_MARSHALLER_SECRET and the HD_X_AS value(s) already used by Gii Murin." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host ".env.local-importer already exists; leaving it unchanged." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "The game credential itself does NOT belong in this file." -ForegroundColor Cyan
Write-Host "The importer reads the newest local DC* file only when you run it."
Write-Host ""
