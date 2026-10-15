param(
    [string]$AssetToolsPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw ".venv is missing. Run .\scripts\setup-local.ps1 first."
}

if (-not $AssetToolsPath) {
    $AssetToolsPath = Join-Path `
        (Split-Path -Parent $ProjectRoot) `
        "holodori-asset-tools"
}

if (-not (Test-Path $AssetToolsPath)) {
    throw "holodori-asset-tools was not found at: $AssetToolsPath"
}

$Python = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "Installing holodori-asset-tools in editable mode..." -ForegroundColor Cyan
Write-Host $AssetToolsPath -ForegroundColor DarkGray

& $Python -m pip install -e $AssetToolsPath

Write-Host ""
Write-Host "Verifying import..." -ForegroundColor Cyan

& $Python -c @"
import holodori_asset_tools
from pathlib import Path
print("Loaded:", Path(holodori_asset_tools.__file__).resolve())
"@

Write-Host ""
Write-Host "Asset tools ready." -ForegroundColor Green
Write-Host "Restart the API, then open:"
Write-Host "  http://127.0.0.1:8000/api/assets/status"
