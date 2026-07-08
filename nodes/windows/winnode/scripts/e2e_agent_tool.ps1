param(
    [string]$Config = 'config.local.yaml',
    [string]$BaseUrl = '',
    [string]$AgentToken = '',
    [string]$Provider = 'deepseek',
    [string]$Prompt = 'find README files on the Windows node using Everything and summarize the top matches',
    [int]$TimeoutSec = 90,
    [switch]$KeepDaemonRunning
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'YeQuWinNode.Common.ps1')

$configPath = Join-Path $Root $Config
$settings = Read-NodeWinConfig -Path $configPath
$base = if ($BaseUrl) { $BaseUrl } else { Get-ConfigValue $settings 'center_base_url' }
$agent = if ($AgentToken) { $AgentToken } else { Get-ConfigValue $settings 'api_token' $env:YEQU_AGENT_TOKEN }

if (-not $agent) {
    throw 'Agent token is required. Pass -AgentToken, set api_token in config, or set YEQU_AGENT_TOKEN.'
}

$daemon = $null
try {
    if (-not $KeepDaemonRunning) {
        $daemon = Start-NodeWinDaemon -Root $Root -Config $configPath
        Start-Sleep -Seconds 4
    }

    $headers = New-BearerHeaders -Token $agent
    $session = Invoke-JsonRequest `
        -Uri "$base/agent/sessions" `
        -Method Post `
        -Headers $headers `
        -Body @{
            actor_id = 'win-node-e2e'
            execution_mode = 'auto'
            max_total_duration_sec = 60
        } `
        -TimeoutSec 30

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-JsonRequest `
        -Uri "$base/agent/invoke" `
        -Method Post `
        -Headers $headers `
        -Body @{
            session_id = $session.session_id
            provider_name = $Provider
            prompt = $Prompt
            execution_mode = 'auto'
        } `
        -TimeoutSec $TimeoutSec
    $sw.Stop()

    $errors = New-Object System.Collections.Generic.List[string]
    if ($response.success -ne $true) {
        $errors.Add("success is not true: $($response.success)")
    }
    if ($response.status -ne 'succeeded') {
        $errors.Add("status is not succeeded: $($response.status)")
    }
    if ($null -ne $response.error) {
        $errors.Add('error is not null')
    }
    if (-not $response.tool_calls -or $response.tool_calls.Count -lt 1) {
        $errors.Add('missing tool_calls')
    } else {
        $tool = $response.tool_calls[0]
        if (-not $tool.invocation_id) {
            $errors.Add('missing invocation_id')
        }
        if (-not $tool.job_ids -or $tool.job_ids.Count -lt 1) {
            $errors.Add('missing job_ids')
        }
        if ($tool.status -ne 'succeeded') {
            $errors.Add("tool status is not succeeded: $($tool.status)")
        }
        if ($tool.name -eq 'windows.everything.find') {
            if ($null -eq $tool.result -or -not ($tool.result.PSObject.Properties.Name -contains 'matches')) {
                $errors.Add('missing result.matches')
            }
        }
    }

    $summary = [pscustomobject]@{
        ok = $errors.Count -eq 0
        elapsed_sec = [math]::Round($sw.Elapsed.TotalSeconds, 3)
        session_id = $session.session_id
        provider = $Provider
        prompt = $Prompt
        response = $response
        errors = $errors
    }
    $summary | ConvertTo-Json -Depth 20

    if ($errors.Count -gt 0) {
        exit 2
    }
} finally {
    if (-not $KeepDaemonRunning) {
        Stop-NodeWinDaemon -Process $daemon
    }
}
