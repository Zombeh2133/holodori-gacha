param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^\d{5,32}$')]
    [string]$DiscordUserId
)

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

$IntegrationKey = (
    $KeyLine -replace "^GII_MURIN_INTEGRATION_KEY=", ""
).Trim()

$Payload = Get-Content `
    ".\test-data\sample-gii-murin-import.json" `
    -Raw | ConvertFrom-Json

$Payload.discord_user_id = $DiscordUserId
$Payload.game_account_id = "OAUTH-CLAIM-TEST"
$Payload.account_nickname = "OAuth Auto-Claim Test"

$Body = $Payload | ConvertTo-Json -Depth 20
$Headers = @{
    "X-Integration-Key" = $IntegrationKey
}

Write-Host ""
Write-Host "Creating pre-login gacha data for Discord ID $DiscordUserId..." -ForegroundColor Cyan

$result = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/integrations/gii-murin/import" `
    -Headers $Headers `
    -ContentType "application/json" `
    -Body $Body

Write-Host ""
$result | Format-List
Write-Host ""
Write-Host "Now log in at http://localhost:8000/auth-test" -ForegroundColor Green
