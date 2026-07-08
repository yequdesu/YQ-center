# YeQu Windows Node

This directory contains the Windows node implementation managed by the main
YeQu Center repository.

## Layout

```text
node_win_client/        Python daemon, YQP client, capability runtime
node_win_client/capabilities/
                        one capability per file
desktop-ui/             local Windows GUI source
scripts/                helper scripts
tests/                  Windows node tests
tools/yq-croc/current/  bundled yq-croc.exe
```

Runtime data, local config, caches, virtual environments, frontend build output,
and logs are not source-controlled.

## Development

```powershell
cd nodes\windows\winnode
python -m pip install -e ".[dev]"
python -m pytest tests/test_plugins.py -q
```

Start the local GUI:

```powershell
.\start-gui.ps1
```

Start from batch:

```powershell
.\start-gui.bat
```

## Configuration

Create a local config from the example:

```powershell
Copy-Item config.example.yaml config.local.yaml
```

`config.local.yaml` is intentionally ignored by git.

## Capability Policy

Windows capabilities are split into:

- Product / workflow capabilities: `windows.transfer.croc.*`,
  `windows.transfer.local.stat`, artifact upload/download, and
  `windows.screen.capture`.
- Core capability: `windows.everything.find` for fast file discovery through
  Everything/es.exe.
- Primitive capability: `windows.exec.run`.

Low-value one-off shell wrappers should not be added as separate capabilities.
Use `windows.exec.run` with an explicit `profile`, command string, and
audit reason. Until the unified command gate exists, Center treats
`windows.exec.run` as a conservative write-like operation and routes it through
normal approval/audit.

`windows.everything.find` is the Windows file discovery capability and must use
Everything/es.exe. Directory listing, file stat, hashing, and text inspection
are handled through `windows.exec.run` with an explicit profile and intent.
