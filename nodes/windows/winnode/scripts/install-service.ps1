param(
    [ValidateSet("Hybrid", "LocalSystem", "CurrentUser", "Credential")]
    [string]$AccountMode = "LocalSystem"
)

$ErrorActionPreference = "Stop"

$serviceName = "YeQuWinClient"
$displayName = "YeQu Windows Client"

$pythonExe = & {
    $venvPy = "$PSScriptRoot\..\.venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($sysPy) { return $sysPy.Source }
    throw "Python not found"
}

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service YeQuWinClient already installed. Stopping and removing..."
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    & $pythonExe -m node_win_client.service_installer remove
    Start-Sleep -Seconds 2
}

$installArgs = @("-m", "node_win_client.service_installer", "install")
$credential = $null
$serviceAccountMode = if ($AccountMode -eq "Hybrid") { "LocalSystem" } else { $AccountMode }
if ($serviceAccountMode -eq "CurrentUser") {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-Host "Installing service to run as current user: $currentUser"
    Write-Host "Enter the Windows password for this account. The password is passed only to the Service Control Manager."
    $credential = Get-Credential -UserName $currentUser -Message "YeQuWinClient service account"
} elseif ($serviceAccountMode -eq "Credential") {
    Write-Host "Installing service to run as a custom Windows account."
    $credential = Get-Credential -Message "YeQuWinClient service account"
}

try {
    if ($credential) {
        $env:YEQU_SERVICE_USERNAME = $credential.UserName
        $env:YEQU_SERVICE_PASSWORD = $credential.GetNetworkCredential().Password
        $installArgs += @("--username-env", "YEQU_SERVICE_USERNAME", "--password-env", "YEQU_SERVICE_PASSWORD")
    }
    & $pythonExe @installArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pywin32 service install failed with code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:\YEQU_SERVICE_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:\YEQU_SERVICE_PASSWORD -ErrorAction SilentlyContinue
}

sc.exe failure $serviceName reset= 86400 actions= restart/60000/restart/300000/restart/900000 | Out-Null
if ($AccountMode -eq "Hybrid") {
    try {
        & (Join-Path $PSScriptRoot "install-user-worker.ps1")
    } catch {
        Write-Warning "User worker install failed: $($_.Exception.Message)"
        Write-Warning "The LocalSystem service was installed. Reopen the GUI and install Hybrid again, or inspect scripts\install-user-worker.ps1."
    }
}

Write-Host "Service YeQuWinClient installed successfully."
