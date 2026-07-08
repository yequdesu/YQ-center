$ErrorActionPreference = "Stop"

$pythonExe = & {
    $venvPy = "$PSScriptRoot\..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    throw "Python not found"
}

$configPath = Join-Path $PSScriptRoot ".." "config.local.yaml"

if (-not (Test-Path $configPath)) {
    Write-Error "Config not found at $configPath. Run init-config.ps1 first."
    exit 1
}

& $pythonExe -m node_win_client.cli diagnostics export -c $configPath
Write-Host "Diagnostics export complete."
