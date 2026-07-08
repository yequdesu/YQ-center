param(
    [string]$TaskName = "YeQuWinClientUserWorker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "User worker task $TaskName is not installed."
    exit 0
}

Start-ScheduledTask -TaskName $TaskName
Write-Host "User worker task $TaskName started."
