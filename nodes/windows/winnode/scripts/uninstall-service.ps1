$ErrorActionPreference = "Stop"
$serviceName = "YeQuWinClient"
& (Join-Path $PSScriptRoot "uninstall-user-worker.ps1")
$pythonExe = & {
    $venvPy = "$PSScriptRoot\..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    throw "Python not found"
}

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -eq "Running") {
        Stop-Service -Name $serviceName -Force
        Start-Sleep -Seconds 3
    }
    & $pythonExe -m node_win_client.service_installer remove
    if ($LASTEXITCODE -ne 0) {
        sc.exe delete $serviceName | Out-Null
    }
    Start-Sleep -Seconds 2
    Write-Host "Service YeQuWinClient uninstalled successfully."
} else {
    Write-Host "Service YeQuWinClient is not installed."
}
