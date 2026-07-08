# YeQuWinClient Service Account

YeQuWinClient supports three local installation modes.

## Recommended mode: Hybrid

Hybrid mode installs:

- `YeQuWinClient` Windows Service as `LocalSystem`
- `YeQuWinClientUserWorker` scheduled task as the current interactive Windows user

The service remains the only process that connects to Center as the Node daemon.
The user worker listens only on `127.0.0.1:9817` and executes user-context tools on behalf of the service.

Use this mode for normal installations:

```powershell
cd E:\yequdesu_project\YQ-center-review\nodes\windows\winnode
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-service-admin-manual.ps1 -AccountMode Hybrid
```

The GUI `Install Service` dialog also exposes this mode as `Hybrid (Recommended)`.

## Default mode: LocalSystem

Use this mode for privileged maintenance tasks, early boot startup, and stable unattended operation.
LocalSystem does not have the same view as the logged-in desktop user. It may not see:

- user profile paths under `C:\Users\<user>`
- mapped network drives
- per-user app settings under HKCU or `%APPDATA%`
- interactive desktop state

## User account mode

When tools must read or modify user-owned files, install the service from an elevated PowerShell window:

```powershell
cd E:\yequdesu_project\YQ-center-review\nodes\windows\winnode
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-service-admin-manual.ps1 -AccountMode CurrentUser
```

The script prompts for the current Windows account password and passes it to the Windows Service Control Manager.
The service then runs as that user account and can see that user's profile paths.

For another service account:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-service-admin-manual.ps1 -AccountMode Credential
```

If Windows rejects the start with a logon error, grant the selected account the `Log on as a service` right and run the install command again.

## Design boundary

Running a Windows service as a user account still does not make it an interactive desktop process. Hybrid mode is the supported way to combine privileged background maintenance with user-profile access without registering two competing Center nodes.
