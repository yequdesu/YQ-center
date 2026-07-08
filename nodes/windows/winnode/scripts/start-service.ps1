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
if (-not $svc) {
    Write-Host "Service YeQuWinClient is not installed. Run install-service.ps1 first."
    exit 1
}
if ($svc.Status -eq "Running") {
    Write-Host "Service YeQuWinClient is already running."
    exit 0
}
& $pythonExe -m node_win_client.service_installer start
if ($LASTEXITCODE -ne 0) {
    throw "service start failed with code $LASTEXITCODE"
}
& (Join-Path $PSScriptRoot "start-user-worker.ps1")
Start-Sleep -Seconds 2
$check = Get-Service -Name $serviceName
Write-Host "Service YeQuWinClient status: $($check.Status)"
