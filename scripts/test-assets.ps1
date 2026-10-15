param(
    [string]$Query = "img_banner_full_gacha-pickup",
    [string]$CardId = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host ""
Write-Host "Holodori Gacha - Asset Integration Test" -ForegroundColor Cyan
Write-Host "========================================"
Write-Host ""

$status = Invoke-RestMethod `
    -Method Get `
    -Uri "http://127.0.0.1:8000/api/assets/status"

$status | Format-List

if (-not $status.available) {
    throw "Asset integration is not available. Run .\scripts\setup-assets.ps1 first."
}

Write-Host ""
Write-Host "Catalog search: $Query" -ForegroundColor Cyan

$encoded = [System.Uri]::EscapeDataString($Query)

$search = Invoke-RestMethod `
    -Method Get `
    -Uri "http://127.0.0.1:8000/api/assets/search?query=$encoded&limit=20"

$search.results | Format-Table group, name, size -AutoSize

if ($CardId) {
    Write-Host ""
    Write-Host "Card candidates: $CardId" -ForegroundColor Cyan

    $card = [System.Uri]::EscapeDataString($CardId)

    $matches = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:8000/api/assets/card/$card/matches?limit=30"

    $matches.results | Format-Table score, group, name, size -AutoSize
}
