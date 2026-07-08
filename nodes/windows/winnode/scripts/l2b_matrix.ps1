param(
    [string]$Config = 'config.local.yaml'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $Root $Config
$exe = Join-Path $Root '.venv\Scripts\node-win.exe'

if (-not (Test-Path -LiteralPath $exe)) {
    $exe = 'node-win'
}

& $exe l2b-matrix -c $configPath
if ($LASTEXITCODE -ne 0) {
    throw "L2-B matrix failed with exit code $LASTEXITCODE"
}
