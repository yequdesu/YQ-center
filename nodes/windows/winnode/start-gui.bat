@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run: python -m venv .venv
    pause
    exit /b 1
)

if not exist "config.local.yaml" (
    echo [INFO] Config not found, initializing...
    call ".venv\Scripts\python.exe" -m node_win_client.cli config init
)

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m node_win_client.cli gui -c config.local.yaml
) else (
    start "" ".venv\Scripts\python.exe" -m node_win_client.cli gui -c config.local.yaml
)
