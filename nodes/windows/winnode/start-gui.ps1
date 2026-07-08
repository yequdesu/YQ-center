$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot ".venv" "Scripts" "pythonw.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = Join-Path $PSScriptRoot ".venv" "Scripts" "python.exe"
}
if (-not (Test-Path $pythonExe)) {
    Write-Error "Virtual environment not found. Run: python -m venv .venv"
    exit 1
}

$configPath = Join-Path $PSScriptRoot "config.local.yaml"
if (-not (Test-Path $configPath)) {
    Write-Host "[INFO] Config not found, initializing..."
    & $pythonExe -m node_win_client.cli config init
}

Write-Host "Starting YeQu Windows Client GUI..."
Start-Process -FilePath $pythonExe -ArgumentList "-m", "node_win_client.cli", "gui", "-c", $configPath
