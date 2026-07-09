$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Get-SystemPython {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            & $pyLauncher.Source -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @($pyLauncher.Source, "-3.11")
            }
        } catch {
            # Continue to plain python lookup.
        }
    }

    foreach ($candidate in @("python.exe", "python3.exe", "python")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) {
            continue
        }
        & $cmd.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return @($cmd.Source)
        }
    }

    throw "Python 3.11+ was not found. Install Python 3.11 or newer, then run start-gui.bat again."
}

function Invoke-SystemPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PythonCommand,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $baseArgs = @()
    if ($PythonCommand.Length -gt 1) {
        $baseArgs = $PythonCommand[1..($PythonCommand.Length - 1)]
    }
    & $PythonCommand[0] @baseArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Arguments -join ' ')"
    }
}

$venvDir = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPythonw = Join-Path $venvDir "Scripts\pythonw.exe"
$sitePackages = Join-Path $venvDir "Lib\site-packages"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[INFO] Creating local virtual environment..."
    $systemPython = Get-SystemPython
    Invoke-SystemPython -PythonCommand $systemPython -Arguments @("-m", "venv", $venvDir)
}

if (Test-Path -LiteralPath $sitePackages) {
    Get-ChildItem -LiteralPath $sitePackages -Filter "yequ_win_client_service.pth" | ForEach-Object {
        $content = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -and $content -match "node_win_client|Node-winClient|winnode") {
            if ($content -notmatch [regex]::Escape($PSScriptRoot)) {
                Write-Host "[INFO] Removing stale Python path file $($_.Name)"
                Remove-Item -LiteralPath $_.FullName -Force
            }
        }
    }
}

$dependencyCheckScript = "import pathlib, node_win_client, httpx, pydantic, yaml, webview, PIL; root = pathlib.Path(r'$PSScriptRoot').resolve(); module = pathlib.Path(node_win_client.__file__).resolve(); raise SystemExit(0 if module.is_relative_to(root) else 1)"
$dependencyCheck = & $venvPython -c $dependencyCheckScript 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Installing YeQu Windows Client dependencies..."
    & $venvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip in $venvDir"
    }
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install YeQu Windows Client into $venvDir"
    }
}

$configPath = Join-Path $PSScriptRoot "config.local.yaml"
if (-not (Test-Path $configPath)) {
    Write-Host "[INFO] Config not found, initializing..."
    & $venvPython -m node_win_client.cli config init
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to initialize config.local.yaml"
    }
}

$pythonExe = $venvPython
if (Test-Path -LiteralPath $venvPythonw) {
    $pythonExe = $venvPythonw
}

Write-Host "Starting YeQu Windows Client GUI..."
Start-Process -FilePath $pythonExe -ArgumentList "-m", "node_win_client.cli", "gui", "-c", $configPath
