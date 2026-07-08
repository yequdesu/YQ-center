param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$CheckScript = ".\scripts\remote_e2e_check.py",
    [string]$LogDir = ".\e2e-logs"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host "[FAIL] This script must run in an elevated PowerShell window." -ForegroundColor Red
    Write-Host ""
    Write-Host "Open PowerShell with 'Run as administrator', then run:" -ForegroundColor Yellow
    Write-Host "  cd `"$Root`""
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_admin_e2e.ps1"
    exit 5
}

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}
if (-not (Test-Path $CheckScript)) {
    throw "Check script not found: $CheckScript"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $LogDir "admin-e2e-$timestamp.json"

Write-Host "[OK] Running as Windows Administrator." -ForegroundColor Green
Write-Host "[INFO] Starting YeQu winClient E2E check..."
Write-Host "[INFO] Output log: $logPath"
Write-Host ""

& $Python $CheckScript | Tee-Object -FilePath $logPath
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[DONE] E2E check finished. Review JSON above and log file." -ForegroundColor Green
} else {
    Write-Host "[FAIL] E2E check exited with code $exitCode." -ForegroundColor Red
}

exit $exitCode
