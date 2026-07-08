param(
    [string]$BaseUrl = "https://gtw.yequdesu.top",
    [string]$AgentToken = "win-agent-pg-e2e-197042c1ca2674edf948fc86",
    [string]$AdminToken = "qq756522327",
    [string]$ServiceName = "Spooler",
    [string]$LogDir = ".\e2e-logs",
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Json {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [string]$Method = "Get",
        [hashtable]$Headers = @{},
        [object]$Body = $null,
        [int]$TimeoutSec = 60
    )

    $args = @{
        Uri = $Uri
        Method = $Method
        Headers = $Headers
        TimeoutSec = $TimeoutSec
    }
    if ($null -ne $Body) {
        $args.ContentType = "application/json; charset=utf-8"
        $args.Body = ($Body | ConvertTo-Json -Depth 30 -Compress)
    }
    return Invoke-RestMethod @args
}

function Wait-RunTerminal {
    param(
        [Parameter(Mandatory = $true)][string]$RunId,
        [hashtable]$Headers,
        [int]$TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $terminal = @("succeeded", "failed", "timeout", "cancelled", "partial", "partially_succeeded", "rollback_recommended")
    do {
        Start-Sleep -Seconds 2
        $detail = Invoke-Json -Uri "$BaseUrl/admin/maintenance/runs/$RunId" -Headers $Headers -TimeoutSec 30
        if ($terminal -contains [string]$detail.status) {
            return $detail
        }
    } while ((Get-Date) -lt $deadline)

    throw "Run $RunId did not reach terminal state within $TimeoutSec seconds."
}

function Get-EventTypes {
    param([object]$Timeline)

    if ($Timeline.PSObject.Properties.Name -contains "value") {
        return @($Timeline.value | ForEach-Object { $_.event_type })
    }
    if ($Timeline.PSObject.Properties.Name -contains "events") {
        return @($Timeline.events | ForEach-Object { $_.event_type })
    }
    if ($Timeline -is [System.Array]) {
        return @($Timeline | ForEach-Object { $_.event_type })
    }
    return @()
}

if (-not (Test-IsAdmin)) {
    Write-Host "[FAIL] This script must run in an elevated PowerShell window." -ForegroundColor Red
    Write-Host ""
    Write-Host "Open PowerShell with 'Run as administrator', then run:" -ForegroundColor Yellow
    Write-Host "  cd `"$Root`""
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_l2c_unhealthy_artifacts_e2e.ps1"
    exit 5
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $LogDir "l2c-unhealthy-artifacts-$timestamp.json"

$agentHeaders = @{ Authorization = "Bearer $AgentToken" }
$adminHeaders = @{ Authorization = "Bearer $AdminToken" }
$daemon = $null

try {
    Write-Host "[OK] Running as Windows Administrator." -ForegroundColor Green
    Write-Host "[INFO] Stopping $ServiceName to create unhealthy state..."
    Stop-Service -Name $ServiceName -Force
    Start-Sleep -Seconds 2
    $beforeService = Get-Service -Name $ServiceName
    if ($beforeService.Status -ne "Stopped") {
        throw "Expected $ServiceName to be Stopped, got $($beforeService.Status)."
    }

    Write-Host "[INFO] Starting winClient daemon..."
    $daemon = Start-Process `
        -FilePath ".\.venv\Scripts\python.exe" `
        -ArgumentList @("-m", "node_win_client.cli", "run", "-c", "config.local.yaml") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Start-Sleep -Seconds 5
    if ($daemon.HasExited) {
        throw "winClient daemon exited early with code $($daemon.ExitCode)."
    }

    Write-Host "[INFO] Creating agent session and check+fix plan..."
    $session = Invoke-Json `
        -Uri "$BaseUrl/agent/sessions" `
        -Method Post `
        -Headers $agentHeaders `
        -Body @{
            actor_id = "l2c-unhealthy-artifacts-e2e"
            execution_mode = "auto"
            max_total_duration_sec = 180
        } `
        -TimeoutSec 30

    $plan = Invoke-Json `
        -Uri "$BaseUrl/agent/plan" `
        -Method Post `
        -Headers $agentHeaders `
        -Body @{
            session_id = $session.session_id
            provider_name = "deepseek"
            prompt = "check print spooler service, if it is not running fix it, then verify"
            target_node_id = "winClient"
            execution_mode = "auto"
            max_total_duration_sec = 180
        } `
        -TimeoutSec 240

    $planId = [string]$plan.plan_id
    if (-not $planId) {
        throw "Agent plan did not return plan_id."
    }
    if ($plan.status -ne "waiting_approval") {
        throw "Expected plan.status=waiting_approval, got $($plan.status)."
    }

    Write-Host "[INFO] Approving plan $planId..."
    $approval = Invoke-Json `
        -Uri "$BaseUrl/admin/maintenance/plans/$planId/approve" `
        -Method Post `
        -Headers $adminHeaders `
        -TimeoutSec 30

    $approvalId = [string]$approval.approval_id
    if (-not $approvalId) {
        throw "Plan approval did not return approval_id."
    }

    Write-Host "[INFO] Running plan $planId..."
    $run = Invoke-Json `
        -Uri "$BaseUrl/admin/maintenance/plans/$planId/run" `
        -Method Post `
        -Headers $adminHeaders `
        -TimeoutSec 60

    $runId = [string]$run.run_id
    if (-not $runId) {
        throw "Plan run did not return run_id."
    }

    $detail = Wait-RunTerminal -RunId $runId -Headers $adminHeaders -TimeoutSec $TimeoutSec
    $artifacts = Invoke-Json `
        -Uri "$BaseUrl/admin/maintenance/runs/$runId/artifacts" `
        -Headers $adminHeaders `
        -TimeoutSec 30
    $checkArtifacts = Invoke-Json `
        -Uri "$BaseUrl/admin/maintenance/runs/$runId/artifacts?kind=check_result" `
        -Headers $adminHeaders `
        -TimeoutSec 30
    $rollbackArtifacts = Invoke-Json `
        -Uri "$BaseUrl/admin/maintenance/runs/$runId/artifacts?kind=rollback_hint" `
        -Headers $adminHeaders `
        -TimeoutSec 30
    $timeline = Invoke-Json `
        -Uri "$BaseUrl/admin/timeline?approval_id=$approvalId&limit=200" `
        -Headers $adminHeaders `
        -TimeoutSec 30

    $afterService = Get-Service -Name $ServiceName
    $steps = @($detail.steps)
    $artifactItems = @($artifacts.artifacts)
    $artifactKinds = @($artifactItems | ForEach-Object { $_.kind })
    $eventTypes = Get-EventTypes -Timeline $timeline

    $requiredKinds = @("check_result", "before", "after", "verify_result", "rollback_hint")
    $missingKinds = @($requiredKinds | Where-Object { $artifactKinds -notcontains $_ })

    $result = [ordered]@{
        ok = (
            $detail.status -eq "succeeded" -and
            $detail.rollback_recommended -eq $false -and
            $detail.summary.succeeded -eq 3 -and
            $detail.summary.skipped -eq 0 -and
            $detail.summary.failed -eq 0 -and
            $steps.Count -eq 3 -and
            $steps[0].status -eq "succeeded" -and
            $steps[1].status -eq "succeeded" -and
            $steps[1].function_name -eq "system.service.ensure_running" -and
            $steps[2].status -eq "succeeded" -and
            $afterService.Status -eq "Running" -and
            $missingKinds.Count -eq 0 -and
            @($checkArtifacts.artifacts).Count -ge 1 -and
            @($rollbackArtifacts.artifacts).Count -ge 1 -and
            $eventTypes -contains "maintenance.artifact.created" -and
            $eventTypes -notcontains "maintenance.rollback.recommended"
        )
        before_service_status = [string]$beforeService.Status
        after_service_status = [string]$afterService.Status
        session_id = $session.session_id
        plan_id = $planId
        approval_id = $approvalId
        run_id = $runId
        plan_status = $plan.status
        run_status = $detail.status
        rollback_recommended = $detail.rollback_recommended
        rollback_hints = $detail.rollback_hints
        artifact_summary = $detail.artifact_summary
        artifact_kinds = $artifactKinds
        missing_artifact_kinds = $missingKinds
        timeline_event_types = $eventTypes
        summary = $detail.summary
        step_statuses = @($steps | ForEach-Object { $_.status })
        step_functions = @($steps | ForEach-Object { $_.function_name })
        skip_reasons = @($steps | ForEach-Object { $_.skip_reason })
        plan = $plan
        approval = $approval
        run_start = $run
        run_detail = $detail
        artifacts = $artifacts
        check_artifacts = $checkArtifacts
        rollback_artifacts = $rollbackArtifacts
        timeline = $timeline
    }

    $json = $result | ConvertTo-Json -Depth 40
    $json | Tee-Object -FilePath $logPath
    Write-Host ""
    Write-Host "[INFO] Log saved to $logPath"
    if ($result.ok) {
        Write-Host "[DONE] L2-C unhealthy artifact E2E passed." -ForegroundColor Green
        exit 0
    }

    Write-Host "[FAIL] L2-C unhealthy artifact E2E did not match expectations." -ForegroundColor Red
    exit 2
} finally {
    if ($null -ne $daemon -and -not $daemon.HasExited) {
        Stop-Process -Id $daemon.Id -Force -ErrorAction SilentlyContinue
    }
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $service -and $service.Status -ne "Running") {
        Write-Host "[INFO] Restoring $ServiceName to Running..."
        Start-Service -Name $ServiceName
    }
}
