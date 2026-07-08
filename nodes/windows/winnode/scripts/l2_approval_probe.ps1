param(
    [string]$Config = 'config.local.yaml',
    [string]$BaseUrl = '',
    [string]$AdminToken = '',
    [string]$ServiceName = 'Spooler',
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

$headers = New-BearerHeaders -Token $admin
$daemon = $null
$functionName = 'system.service.restart'
$inputPayload = @{ name = $ServiceName }

try {
    if (-not $KeepDaemonRunning) {
        $daemon = Start-NodeWinDaemon -Root $Root -Config $configPath
        Start-Sleep -Seconds 4
    }

    $waiting = Invoke-JsonRequest `
        -Uri "$base/admin/invocations" `
        -Method Post `
        -Headers $headers `
        -Body @{
            function_name = $functionName
            target_node_id = $nodeId
            input = $inputPayload
        } `
        -TimeoutSec 30

    if ($waiting.invocation_status -ne 'waiting_approval') {
        throw "Expected waiting_approval, got $($waiting.invocation_status)"
    }
    if (-not $waiting.approval_id) {
        throw 'Expected approval_id from L2 invocation.'
    }

    $approvalId = [string]$waiting.approval_id
    $approved = Invoke-JsonRequest `
        -Uri "$base/admin/approvals/$approvalId/approve" `
        -Method Post `
        -Headers $headers `
        -Body @{ approved_by = 'win-node-probe' } `
        -TimeoutSec 30

    if ($approved.status -ne 'approved') {
        throw "Expected approved status, got $($approved.status)"
    }

    $execution = Invoke-JsonRequest `
        -Uri "$base/admin/invocations" `
        -Method Post `
        -Headers $headers `
        -Body @{
            function_name = $functionName
            target_node_id = $nodeId
            approval_id = $approvalId
            input = $inputPayload
        } `
        -TimeoutSec 30

    if (-not $execution.job_id) {
        throw 'Expected approved invocation to create a Job.'
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $job = $null
    do {
        Start-Sleep -Seconds 1
        $jobs = Invoke-JsonRequest -Uri "$base/admin/jobs" -Headers $headers -TimeoutSec 30
        $job = $jobs | Where-Object { $_.job_id -eq $execution.job_id } | Select-Object -First 1
        if ($job -and $job.status -in @('succeeded', 'failed', 'timeout', 'cancelled')) {
            break
        }
    } while ((Get-Date) -lt $deadline)

    if (-not $job) {
        throw "Job $($execution.job_id) not found."
    }
    if ($job.status -ne 'succeeded') {
        throw "Expected succeeded job, got $($job.status)"
    }
    if (-not $job.output.dry_run) {
        throw 'Probe must stay dry_run=true; node returned dry_run=false.'
    }

    $consumed = Invoke-JsonRequest `
        -Uri "$base/admin/approvals/$approvalId" `
        -Headers $headers `
        -TimeoutSec 30

    if ($consumed.status -ne 'consumed') {
        throw "Expected consumed approval, got $($consumed.status)"
    }

    $reuseRejected = $false
    try {
        Invoke-JsonRequest `
            -Uri "$base/admin/invocations" `
            -Method Post `
            -Headers $headers `
            -Body @{
                function_name = $functionName
                target_node_id = $nodeId
                approval_id = $approvalId
                input = $inputPayload
            } `
            -TimeoutSec 30 | Out-Null
    } catch {
        $reuseRejected = $true
    }

    if (-not $reuseRejected) {
        throw 'Consumed approval was reused successfully; expected rejection.'
    }

    $locks = Invoke-JsonRequest -Uri "$base/admin/locks" -Headers $headers -TimeoutSec 30
    $lock = $locks |
        Where-Object { $_.job_id -eq $execution.job_id } |
        Select-Object -First 1

    if (-not $lock) {
        throw "No ResourceLock found for $($execution.job_id)."
    }
    if ($lock.status -ne 'released') {
        throw "Expected released lock, got $($lock.status)"
    }

    [pscustomobject]@{
        ok = $true
        waiting_invocation_id = $waiting.invocation_id
        approval_id = $approvalId
        execution_invocation_id = $execution.invocation_id
        job_id = $execution.job_id
        job_status = $job.status
        dry_run = $job.output.dry_run
        lock_key = $lock.resource_key
        lock_status = $lock.status
        consumed_reuse_rejected = $reuseRejected
    } | ConvertTo-Json -Depth 10
} finally {
    if (-not $KeepDaemonRunning) {
        Stop-NodeWinDaemon -Process $daemon
    }
}
