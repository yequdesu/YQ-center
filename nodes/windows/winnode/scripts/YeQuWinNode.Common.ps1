Set-StrictMode -Version Latest

function Get-NodeWinRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return Split-Path -Parent $scriptDir
}

function Read-NodeWinConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolved = Resolve-Path -LiteralPath $Path
    $config = @{}
    foreach ($line in Get-Content -LiteralPath $resolved) {
        if ($line -match '^\s*#' -or $line -match '^\s*$') {
            continue
        }
        if ($line -match '^\s*([A-Za-z0-9_.-]+):\s*"?([^"#]+?)"?\s*$') {
            $config[$Matches[1]] = $Matches[2].Trim()
        }
    }
    return $config
}

function Get-NodeWinExe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $venvExe = Join-Path $Root '.venv\Scripts\node-win.exe'
    if (Test-Path -LiteralPath $venvExe) {
        return $venvExe
    }
    return 'node-win'
}

function Get-ConfigValue {
    param(
        [hashtable]$Config,
        [string]$Key,
        [string]$Default = ''
    )

    if ($Config.ContainsKey($Key) -and $Config[$Key]) {
        return [string]$Config[$Key]
    }
    return $Default
}

function New-BearerHeaders {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    return @{ Authorization = "Bearer $Token" }
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [string]$Method = 'Get',
        [hashtable]$Headers = @{},
        [object]$Body = $null,
        [int]$TimeoutSec = 30
    )

    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -Headers $Headers -TimeoutSec $TimeoutSec
    }

    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    return Invoke-RestMethod `
        -Uri $Uri `
        -Method $Method `
        -Headers $Headers `
        -ContentType 'application/json' `
        -Body $json `
        -TimeoutSec $TimeoutSec
}

function Start-NodeWinDaemon {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Config
    )

    $exe = Get-NodeWinExe -Root $Root
    return Start-Process `
        -FilePath $exe `
        -ArgumentList @('run', '-c', $Config) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
}

function Stop-NodeWinDaemon {
    param(
        [object]$Process
    )

    if ($null -ne $Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
    }
}
