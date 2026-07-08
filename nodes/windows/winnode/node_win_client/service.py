from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.request import urlopen

SERVICE_NAME = "YeQuWinClient"
DISPLAY_NAME = "YeQu Windows Client"
USER_WORKER_TASK_NAME = "YeQuWinClientUserWorker"
USER_WORKER_HEALTH_URL = "http://127.0.0.1:9817/health"


def _subprocess_creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _find_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_as_admin(script: str, script_args: list[str] | None = None) -> int:
    project_root = _find_project_root()
    script_path = project_root / "scripts" / script
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    args = list(script_args or [])

    temp_dir = Path(tempfile.gettempdir())
    run_id = uuid.uuid4().hex
    result_path = temp_dir / f"yequ-winclient-admin-{run_id}.json"
    output_path = temp_dir / f"yequ-winclient-admin-{run_id}.log"
    tmp_path = temp_dir / f"yequ-winclient-admin-{run_id}.ps1"
    param_entries: list[str] = []
    index = 0
    while index < len(args):
        key = str(args[index])
        if key.startswith("-") and index + 1 < len(args):
            param_entries.append(f"{key.lstrip('-')} = {json.dumps(str(args[index + 1]))}")
            index += 2
        else:
            index += 1
    ps_params = "; ".join(param_entries)
    invoke_script = (
        f"  & {json.dumps(str(script_path))} @scriptParams *>&1 | "
        "Tee-Object -FilePath $outputPath"
    )
    check_exit = (
        "  if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { "
        'throw "script exited with code $LASTEXITCODE" }'
    )
    read_output = (
        "  $output = if (Test-Path $outputPath) { "
        "Get-Content -LiteralPath $outputPath -Raw } else { '' }"
    )
    write_success = (
        "  @{ ok = $true; output = $output } | ConvertTo-Json -Compress | "
        "Set-Content -LiteralPath $resultPath -Encoding UTF8"
    )
    write_failure = (
        "  @{ ok = $false; error = $_.Exception.Message; output = $output } | "
        "ConvertTo-Json -Compress | Set-Content -LiteralPath $resultPath -Encoding UTF8"
    )
    tmp_path.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$resultPath = {json.dumps(str(result_path))}",
                f"$outputPath = {json.dumps(str(output_path))}",
                f"$scriptParams = @{{ {ps_params} }}",
                "try {",
                f"  Set-Location -LiteralPath {json.dumps(str(project_root))}",
                invoke_script,
                check_exit,
                read_output,
                write_success,
                "  exit 0",
                "} catch {",
                read_output,
                write_failure,
                "  exit 1",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    try:
        launch_command = (
            "$argsList = @("
            "'-NoProfile',"
            "'-ExecutionPolicy','Bypass',"
            "'-File',"
            f"{json.dumps(str(tmp_path))}"
            "); "
            "$p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru "
            "-WindowStyle Hidden -ArgumentList $argsList; "
            "exit $p.ExitCode"
        )
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                launch_command,
            ],
            capture_output=True,
            creationflags=_subprocess_creation_flags(),
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Script {script} failed: {result.stderr or result.stdout}")
        if not result_path.exists():
            raise RuntimeError(
                f"Admin operation did not return a result. "
                f"UAC may have been cancelled or PowerShell failed before running {script}. "
                f"Wrapper: {tmp_path}; output: {output_path}; result: {result_path}"
            )
        raw_result = result_path.read_text(encoding="utf-8-sig").strip()
        if not raw_result:
            output = (
                output_path.read_text(encoding="utf-8", errors="replace").strip()
                if output_path.exists()
                else ""
            )
            raise RuntimeError(
                f"Admin operation returned an empty result while running {script}. "
                f"{output or 'No output was captured.'}"
            )
        try:
            data = json.loads(raw_result)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Admin operation returned invalid JSON while running {script}: {raw_result[:500]}"
            ) from exc
        if not data.get("ok"):
            output = str(data.get("output") or "").strip()
            error = str(data.get("error") or "").strip()
            message = error or f"Script {script} failed"
            if output:
                message = f"{message}\n\n{output[-4000:]}"
            raise RuntimeError(str(message))
        return result.returncode
    finally:
        keep_files = False
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8-sig").strip() or "{}")
                keep_files = not bool(data.get("ok"))
            except Exception:
                keep_files = True
        else:
            keep_files = True
        for path in (Path(tmp_path), result_path, output_path):
            if keep_files and path.exists():
                continue
            with suppress(Exception):
                path.unlink()


def get_service_status() -> dict:
    try:
        command = (
            "$svc = Get-CimInstance -ClassName Win32_Service "
            f"-Filter \"Name='{SERVICE_NAME}'\" -ErrorAction SilentlyContinue; "
            "if ($svc) { "
            "$svc | Select-Object Name,State,StartMode,DisplayName,PathName,StartName "
            "| ConvertTo-Json -Compress }"
        )
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                command,
            ],
            capture_output=True,
            creationflags=_subprocess_creation_flags(),
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return {
                    "installed": True,
                    "running": data.get("State") == "Running",
                    "startup_type": data.get("StartMode", "Unknown"),
                    "display_name": data.get("DisplayName", DISPLAY_NAME),
                    "path_name": data.get("PathName", ""),
                    "service_account": data.get("StartName", ""),
                    "user_worker": get_user_worker_status(),
                }
        return {
            "installed": False,
            "running": False,
            "startup_type": "N/A",
            "display_name": DISPLAY_NAME,
            "service_account": "",
            "user_worker": get_user_worker_status(),
        }
    except Exception:
        return {
            "installed": False,
            "running": False,
            "startup_type": "N/A",
            "display_name": DISPLAY_NAME,
            "service_account": "",
            "user_worker": get_user_worker_status(),
            "error": "Unable to query service status",
        }


def get_user_worker_status() -> dict:
    task_state = "NotInstalled"
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$task = Get-ScheduledTask -TaskName '{USER_WORKER_TASK_NAME}' "
                    "-ErrorAction SilentlyContinue; "
                    "if ($task) { $task.State }"
                ),
            ],
            capture_output=True,
            creationflags=_subprocess_creation_flags(),
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            task_state = result.stdout.strip()
    except Exception:
        task_state = "Unknown"

    health: dict = {}
    reachable = False
    try:
        with urlopen(USER_WORKER_HEALTH_URL, timeout=1.5) as response:
            health = json.loads(response.read().decode("utf-8"))
            reachable = bool(health.get("ok"))
    except Exception:
        reachable = False

    return {
        "installed": task_state != "NotInstalled",
        "task_state": task_state,
        "running": reachable,
        "reachable": reachable,
        "user": health.get("user"),
        "pid": health.get("pid"),
        "url": USER_WORKER_HEALTH_URL,
    }


def _run_service_script(script: str, script_args: list[str] | None = None) -> dict:
    args = list(script_args or [])
    if is_admin():
        project_root = _find_project_root()
        script_path = project_root / "scripts" / script
        if not script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                *args,
            ],
            cwd=str(project_root),
            capture_output=True,
            creationflags=_subprocess_creation_flags(),
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Script {script} failed: {result.stderr or result.stdout}")
        return {"ok": True, "output": result.stdout}
    else:
        _run_as_admin(script, args)
        return {"ok": True}


def _run_user_script(script: str) -> dict:
    project_root = _find_project_root()
    script_path = project_root / "scripts" / script
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        cwd=str(project_root),
        capture_output=True,
        creationflags=_subprocess_creation_flags(),
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Script {script} failed: {result.stderr or result.stdout}")
    return {"ok": True, "output": result.stdout}


def install_service(account_mode: str = "LocalSystem") -> dict:
    if account_mode not in {"Hybrid", "LocalSystem", "CurrentUser", "Credential"}:
        raise ValueError(f"Unsupported service account mode: {account_mode}")
    return _run_service_script("install-service.ps1", ["-AccountMode", account_mode])


def install_user_worker() -> dict:
    return _run_user_script("install-user-worker.ps1")


def start_user_worker() -> dict:
    return _run_user_script("start-user-worker.ps1")


def stop_user_worker() -> dict:
    return _run_user_script("stop-user-worker.ps1")


def uninstall_service() -> dict:
    return _run_service_script("uninstall-service.ps1")


def start_service() -> dict:
    return _run_service_script("start-service.ps1")


def stop_service() -> dict:
    return _run_service_script("stop-service.ps1")


def restart_service() -> dict:
    stop_service()
    start_service()
    return {"ok": True}
