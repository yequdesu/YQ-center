param(
    [string]$Config = 'config.local.yaml',
    [string]$BaseUrl = '',
    [string]$AdminToken = '',
    [switch]$SkipAdminCheck
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'YeQuWinNode.Common.ps1')

$configPath = Join-Path $Root $Config
$settings = Read-NodeWinConfig -Path $configPath
$base = if ($BaseUrl) { $BaseUrl } else { Get-ConfigValue $settings 'center_base_url' }
$admin = if ($AdminToken) { $AdminToken } else { $env:YEQU_ADMIN_TOKEN }
$nodeId = Get-ConfigValue $settings 'node_id' 'winClient'

Write-Host "Registering capabilities from $nodeId to $base"
$exe = Get-NodeWinExe -Root $Root
& $exe once -c $configPath
if ($LASTEXITCODE -ne 0) {
    throw "node-win once failed with exit code $LASTEXITCODE"
}

if (-not $SkipAdminCheck) {
    if (-not $admin) {
        throw 'Admin token is required for capability verification. Pass -AdminToken or set YEQU_ADMIN_TOKEN.'
    }

    $headers = New-BearerHeaders -Token $admin
    $caps = Invoke-JsonRequest -Uri "$base/admin/capabilities" -Headers $headers -TimeoutSec 30
    $functions = @($caps | Where-Object { $_.capability_type -eq 'function' -and $_.is_active })
    $signals = @($caps | Where-Object { $_.capability_type -eq 'signal' -and $_.is_active })

    $expected = @(
        'windows.artifact.download_file',
        'windows.exec.run',
        'windows.file.upload_artifact',
        'windows.everything.find',
        'windows.screen.capture',
        'windows.transfer.croc.receive',
        'windows.transfer.croc.reconcile',
        'windows.transfer.croc.send',
        'windows.transfer.croc.status',
        'windows.transfer.local.stat'
    )
    $names = @($functions | Select-Object -ExpandProperty name)
    $missing = @($expected | Where-Object { $_ -notin $names })

    [pscustomobject]@{
        node_id = $nodeId
        function_count = $functions.Count
        signal_count = $signals.Count
        missing = $missing
    } | ConvertTo-Json -Depth 8

    if ($missing.Count -gt 0) {
        throw "Missing active functions: $($missing -join ', ')"
    }
}

Write-Host 'Capability registration check passed.'
