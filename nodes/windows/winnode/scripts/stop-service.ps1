$ErrorActionPreference = "Stop"
$serviceName = "YeQuWinClient"
$pythonExe = & {
    $venvPy = "$PSScriptRoot\..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    throw "Python not found"
}

$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot "stop-user-worker.ps1")
if (-not $svc) {
    Write-Host "Service YeQuWinClient is not installed."
    exit 0
}
if ($svc.Status -ne "Running") {
    Write-Host "Service YeQuWinClient is already stopped."
    exit 0
}
& $pythonExe -m node_win_client.service_installer stop
if ($LASTEXITCODE -ne 0) {
    throw "service stop failed with code $LASTEXITCODE"
}
Start-Sleep -Seconds 2
$check = Get-Service -Name $serviceName
Write-Host "Service YeQuWinClient status: $($check.Status)"
