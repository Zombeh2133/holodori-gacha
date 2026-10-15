$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    throw ".env is missing."
}

$KeyLine = Get-Content ".env" | Where-Object {
    $_ -match "^GII_MURIN_INTEGRATION_KEY="
} | Select-Object -First 1

if (-not $KeyLine) {
    throw "GII_MURIN_INTEGRATION_KEY is missing from .env"
}

$IntegrationKey = ($KeyLine -replace "^GII_MURIN_INTEGRATION_KEY=", "").Trim()
$Body = Get-Content ".\test-data\sample-gii-murin-import.json" -Raw
$Headers = @{ "X-Integration-Key" = $IntegrationKey }

Write-Host ""
Write-Host "Uploading sample pull history..." -ForegroundColor Cyan

$result = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/integrations/gii-murin/import" `
    -Headers $Headers `
    -ContentType "application/json" `
    -Body $Body

Write-Host ""
$result | Format-List
