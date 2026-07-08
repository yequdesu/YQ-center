$ErrorActionPreference = "Stop"

$configPath = Join-Path $PSScriptRoot ".." "config.local.yaml"

if (Test-Path $configPath) {
    Write-Host "Config already exists at $configPath"
    $response = Read-Host "Overwrite? (y/N)"
    if ($response -ne "y" -and $response -ne "Y") {
        exit 0
    }
}

$pythonExe = & {
    $venvPy = "$PSScriptRoot\..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    throw "Python not found"
}

& $pythonExe -m node_win_client.cli config init -o $configPath
Write-Host "Config initialized at $configPath"
