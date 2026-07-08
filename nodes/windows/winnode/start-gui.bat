@echo off
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-gui.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] YeQu Windows Client failed to start.
    pause
    exit /b 1
)
