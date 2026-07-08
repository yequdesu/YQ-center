param(
    [string]$TaskName = "YeQuWinClientUserWorker"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$pythonExe = & {
    $venvPy = Join-Path $root ".venv\Scripts\pythonw.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $venvPyConsole = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venvPyConsole) { return (Resolve-Path $venvPyConsole).Path }
    $sysPy = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    $sysPyConsole = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPyConsole) { return $sysPyConsole.Source }
    throw "Python not found"
}

$configPath = Join-Path $root "config.local.yaml"
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$argList = "-m node_win_client.user_worker --config `"$configPath`" --host 127.0.0.1 --port 9817"
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $argList -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 30) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "YeQu Windows Client user-session worker for user-profile tools." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "User worker task $TaskName installed and started."
