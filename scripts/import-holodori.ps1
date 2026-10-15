param(
    [string]$ImportToken = "",
    [string]$ApiBase = "http://127.0.0.1:8000",
    [ValidateSet("", "US", "ASIA", "JP")]
    [string]$Region = "",
    [switch]$ExportOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw ".venv is missing. Run .\scripts\setup-local.ps1 first."
}

$ArgsList = @(
    ".\local_importer\import_holodori.py",
    "--api-base",
    $ApiBase
)

if ($ImportToken) {
    $ArgsList += @(
        "--token",
        $ImportToken
    )
}

if ($Region) {
    $ArgsList += @(
        "--region",
        $Region
    )
}

if ($ExportOnly) {
    $ArgsList += "--export-only"
}

& $Python @ArgsList

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
