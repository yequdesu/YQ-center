param(
    [ValidateSet("Hybrid", "LocalSystem", "CurrentUser", "Credential")]
    [string]$AccountMode = "LocalSystem"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $root

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Host "This script must be run from an elevated PowerShell window." -ForegroundColor Red
    Write-Host "Right-click PowerShell, choose 'Run as administrator', then run this script again."
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Installing YeQuWinClient service..."
& (Join-Path $PSScriptRoot "install-service.ps1") -AccountMode $AccountMode

Write-Host "Starting YeQuWinClient service..."
try {
    & (Join-Path $PSScriptRoot "start-service.ps1")
} catch {
    Write-Host ""
    Write-Host "Service start failed. Diagnostic data follows." -ForegroundColor Red

    Write-Host ""
    Write-Host "[Service Registry]"
    Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\YeQuWinClient" |
        Select-Object ImagePath, Environment |
        Format-List

    Write-Host ""
    Write-Host "[PythonClass]"
    Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\YeQuWinClient\PythonClass" -ErrorAction SilentlyContinue |
        Format-List

    Write-Host ""
    Write-Host "[PythonPath]"
    Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\YeQuWinClient\PythonPath" -ErrorAction SilentlyContinue |
        Format-List

    Write-Host ""
    Write-Host "[Recent Python Service Events]"
    Get-EventLog -LogName Application -Source "Python Service" -Newest 8 -ErrorAction SilentlyContinue |
        Select-Object TimeGenerated,EntryType,EventID,ReplacementStrings |
        Format-List

    $startupLog = Join-Path $root "logs\service-startup.log"
    if (Test-Path $startupLog) {
        Write-Host ""
        Write-Host "[logs\service-startup.log tail]"
        Get-Content -LiteralPath $startupLog -Tail 80
    }

    throw
}

Write-Host ""
Write-Host "Current service state:"
Get-CimInstance -ClassName Win32_Service -Filter "Name='YeQuWinClient'" |
    Select-Object Name,State,StartMode,DisplayName,PathName |
    Format-List

Write-Host ""
Write-Host "Done. Reopen the GUI and refresh the Service page."
Read-Host "Press Enter to exit"
