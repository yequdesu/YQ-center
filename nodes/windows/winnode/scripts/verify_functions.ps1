param(
    [string]$Config = 'config.local.yaml',
    [string]$BaseUrl = '',
    [string]$AdminToken = '',
    [int]$TimeoutSec = 60,
    [switch]$KeepDaemonRunning
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'YeQuWinNode.Common.ps1')

$configPath = Join-Path $Root $Config
$settings = Read-NodeWinConfig -Path $configPath
$base = if ($BaseUrl) { $BaseUrl } else { Get-ConfigValue $settings 'center_base_url' }
$admin = if ($AdminToken) { $AdminToken } else { $env:YEQU_ADMIN_TOKEN }
$nodeId = Get-ConfigValue $settings 'node_id' 'winClient'

if (-not $admin) {
    throw 'Admin token is required. Pass -AdminToken or set YEQU_ADMIN_TOKEN.'
}

$cases = @(
    @{ name = 'windows.exec.run'; input = @{ profile = 'user.readonly'; command = 'whoami'; intent = @{ effect = 'read'; reason = 'verify Windows exec runtime' } } },
    @{ name = 'windows.everything.find'; input = @{ query = 'README'; limit = 3 } },
    @{ name = 'windows.transfer.croc.status'; input = @{} }
)

$daemon = $null
try {
    if (-not $KeepDaemonRunning) {
        $daemon = Start-NodeWinDaemon -Root $Root -Config $configPath
        Start-Sleep -Seconds 4
    }

    $headers = New-BearerHeaders -Token $admin
    $rows = @()
    foreach ($case in $cases) {
        $invocation = Invoke-JsonRequest `
            -Uri "$base/admin/invocations" `
            -Method Post `
            -Headers $headers `
            -Body @{
                function_name = $case.name
                target_node_id = $nodeId
                input = $case.input
            } `
            -TimeoutSec 30

        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        $job = $null
        do {
            Start-Sleep -Seconds 1
            $jobs = Invoke-JsonRequest -Uri "$base/admin/jobs" -Headers $headers -TimeoutSec 30
            $job = $jobs | Where-Object { $_.job_id -eq $invocation.job_id } | Select-Object -First 1
            if ($job -and $job.status -in @('succeeded', 'failed', 'timeout', 'cancelled')) {
                break
            }
        } while ((Get-Date) -lt $deadline)

        $rows += [pscustomobject]@{
            function = $case.name
            invocation_id = $invocation.invocation_id
            job_id = $invocation.job_id
            status = if ($job) { $job.status } else { 'missing' }
        }
    }

    $failed = @($rows | Where-Object { $_.status -ne 'succeeded' })
    [pscustomobject]@{
        ok = $failed.Count -eq 0
        total = $rows.Count
        succeeded = @($rows | Where-Object { $_.status -eq 'succeeded' }).Count
        failed = $failed.Count
        results = $rows
    } | ConvertTo-Json -Depth 20

    if ($failed.Count -gt 0) {
        exit 2
    }
} finally {
    if (-not $KeepDaemonRunning) {
        Stop-NodeWinDaemon -Process $daemon
    }
}
