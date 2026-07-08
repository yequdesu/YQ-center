from __future__ import annotations

import sys
import time
import os
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32service
import win32serviceutil

SERVICE_NAME = "YeQuWinClient"
DISPLAY_NAME = "YeQu Windows Client"
PYTHON_CLASS = "node_win_client.service_daemon.YeQuWinService"
DESCRIPTION = "YeQu Windows Client daemon service. Runs the local node and communicates with Center."
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_SITE_PACKAGES = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"


def _python_path_entries() -> list[str]:
    candidates = [
        PROJECT_ROOT,
        VENV_SITE_PACKAGES,
        VENV_SITE_PACKAGES / "win32",
        VENV_SITE_PACKAGES / "win32" / "lib",
        VENV_SITE_PACKAGES / "pythonwin",
    ]
    return [str(path) for path in candidates if path.exists()]


def _set_registry_default(subkey: str, value: str) -> None:
    key = win32api.RegCreateKey(win32con.HKEY_LOCAL_MACHINE, subkey)
    try:
        win32api.RegSetValue(key, None, win32con.REG_SZ, value)
    finally:
        win32api.RegCloseKey(key)


def _write_service_environment() -> None:
    python_path = ";".join(_python_path_entries())
    service_key_path = rf"System\CurrentControlSet\Services\{SERVICE_NAME}"
    key = win32api.RegOpenKeyEx(
        win32con.HKEY_LOCAL_MACHINE,
        service_key_path,
        0,
        win32con.KEY_SET_VALUE,
    )
    try:
        win32api.RegSetValueEx(
            key,
            "Environment",
            0,
            win32con.REG_MULTI_SZ,
            [
                f"PYTHONPATH={python_path}",
                f"YEQU_WINCLIENT_ROOT={PROJECT_ROOT}",
            ],
        )
    finally:
        win32api.RegCloseKey(key)

    _set_registry_default(
        rf"{service_key_path}\PythonPath",
        python_path,
    )


def _write_project_pth() -> None:
    if not VENV_SITE_PACKAGES.exists():
        return
    pth_path = VENV_SITE_PACKAGES / "yequ_win_client_service.pth"
    pth_path.write_text(str(PROJECT_ROOT) + "\n", encoding="utf-8")


def install(user_name: str | None = None, password: str | None = None) -> None:
    remove_if_exists()
    _write_project_pth()
    if bool(user_name) != bool(password):
        raise ValueError("user_name and password must be provided together")
    win32serviceutil.InstallService(
        PYTHON_CLASS,
        SERVICE_NAME,
        DISPLAY_NAME,
        startType=win32service.SERVICE_AUTO_START,
        userName=user_name,
        password=password,
        description=DESCRIPTION,
    )
    _write_service_environment()
    print(f"installed {SERVICE_NAME} -> {PYTHON_CLASS}")
    print(f"service account -> {user_name or 'LocalSystem'}")
    print(f"service PYTHONPATH -> {';'.join(_python_path_entries())}")


def remove_if_exists() -> None:
    try:
        win32serviceutil.StopService(SERVICE_NAME)
        time.sleep(2)
    except pywintypes.error:
        pass
    try:
        win32serviceutil.RemoveService(SERVICE_NAME)
        time.sleep(2)
        print(f"removed existing {SERVICE_NAME}")
    except pywintypes.error as exc:
        # 1060 = service does not exist
        if exc.winerror != 1060:
            raise


def remove() -> None:
    remove_if_exists()
    print(f"removed {SERVICE_NAME}")


def start() -> None:
    win32serviceutil.StartService(SERVICE_NAME)
    print(f"started {SERVICE_NAME}")


def stop() -> None:
    win32serviceutil.StopService(SERVICE_NAME)
    print(f"stopped {SERVICE_NAME}")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(
            "usage: python -m node_win_client.service_installer "
            "install [--username-env NAME --password-env NAME]|remove|start|stop"
        )
        return 2
    command = args[0].lower()
    if command == "install":
        user_name = None
        password = None
        rest = args[1:]
        while rest:
            flag = rest.pop(0)
            if flag == "--username-env" and rest:
                user_name = os.getenv(rest.pop(0))
            elif flag == "--password-env" and rest:
                password = os.getenv(rest.pop(0))
            else:
                print(f"unknown install option: {flag}")
                return 2
        install(user_name=user_name, password=password)
    elif command in {"remove", "uninstall"}:
        remove()
    elif command == "start":
        start()
    elif command == "stop":
        stop()
    else:
        print(f"unknown command: {command}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
