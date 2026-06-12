#!/usr/bin/env python3
"""YeQu Executor Service — 本机通用指令执行器。

通过名为 script 的壳执行 Agent 下发的任意操作。
Scripts 位于 ./scripts/ 目录，每个可执行文件即一个可调用能力。
"""

import atexit
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import requests

GATEWAY = os.environ.get("YEQ_GATEWAY", "http://127.0.0.1:9800")
DEVICE_ID = f"{socket.gethostname()}-executor"
HEARTBEAT_INTERVAL = 60
COMMAND_POLL_INTERVAL = 3
SCRIPT_TIMEOUT = 30
TOKEN = None
running = True

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")


def _list_scripts():
    """Return all executable scripts with their descriptions."""
    scripts = []
    if not os.path.isdir(SCRIPT_DIR):
        return scripts
    for f in sorted(os.listdir(SCRIPT_DIR)):
        path = os.path.join(SCRIPT_DIR, f)
        if not os.access(path, os.X_OK):
            continue
        desc = ""
        try:
            with open(path) as fh:
                first = fh.readline()
                if first.startswith("# description:") or first.startswith("# description："):
                    desc = first.split(":", 1)[1].strip()
        except Exception:
            pass
        scripts.append({"name": f, "description": desc})
    return scripts


def _exec_script(name, args):
    """Run a named script with optional arguments. Returns {status, output}."""
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.isfile(path):
        return {"status": "error", "output": f"Script not found: {name}"}
    if not os.access(path, os.X_OK):
        return {"status": "error", "output": f"Script not executable: {name}"}
    try:
        cmd = [path] + (args or [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
        return {
            "status": "ok" if r.returncode == 0 else "error",
            "output": (r.stdout + r.stderr)[:16384],
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": f"Script timed out ({SCRIPT_TIMEOUT}s)"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def execute(action, params):
    try:
        if action == "list_scripts":
            return {"status": "ok", "scripts": _list_scripts()}
        elif action == "exec":
            name = params.get("script", "")
            args = params.get("args", [])
            if not name:
                return {"status": "error", "output": "script name is required"}
            return _exec_script(name, args)
        elif action in ("ping", "set_interval", "restart_collector"):
            return {"status": "ok", "output": action}
        else:
            return {"status": "error", "output": f"Unknown action: {action}"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def api(method, path, data=None):
    url = f"{GATEWAY}{path}"
    try:
        if method == "GET":
            return requests.get(url, params=data, timeout=10).json()
        else:
            return requests.post(url, json=data, timeout=10).json()
    except Exception:
        return {"status": "error"}


def register():
    global TOKEN
    retry = 0
    while TOKEN is None and running:
        resp = api("POST", "/hello", {
            "protocol": "yqp/1.0", "message_type": "hello",
            "hello_type": "registration", "device_id": DEVICE_ID,
            "device_info": {
                "os": platform.system(), "hostname": socket.gethostname(),
                "source_type": "service",
            },
            "actions": [
                {
                    "name": "list_scripts",
                    "display": "列出可用脚本",
                    "description": "返回 scripts/ 目录中所有可执行脚本及其说明",
                    "params": {},
                },
                {
                    "name": "exec",
                    "display": "执行脚本",
                    "description": "执行 scripts/ 目录中的指定脚本。先用 list_scripts 查看有哪些可用。",
                    "params": {
                        "script": {"type": "string", "description": "脚本文件名"},
                        "args": {"type": "array", "description": "传递给脚本的参数列表"},
                    },
                },
            ],
        })
        if resp.get("status") == "approved":
            TOKEN = resp["token"]
            available = _list_scripts()
            print(f"[executor] Approved. {len(available)} scripts available: {[s['name'] for s in available]}")
            return
        retry += 1
        wait = resp.get("retry_after", 30)
        if retry <= 10:
            print(f"[executor] Pending (attempt {retry}), retry in {wait}s")
        time.sleep(wait)


def heartbeat_loop():
    while running:
        try:
            api("POST", "/hello", {
                "hello_type": "heartbeat", "device_id": DEVICE_ID,
                "token": TOKEN, "protocol": "yqp/1.0", "message_type": "hello",
            })
        except Exception:
            pass
        time.sleep(HEARTBEAT_INTERVAL)


def command_loop():
    while running:
        try:
            resp = api("GET", "/commands/pending", {
                "device_id": DEVICE_ID, "token": TOKEN,
            })
            cmds = resp.get("pending_commands", [])
            if cmds:
                results = []
                for cmd in cmds:
                    action = cmd["action"]
                    params = cmd.get("params", {})
                    print(f"[executor] {action} {params.get('script','')}")
                    r = execute(action, params)
                    results.append({
                        "command_id": cmd["command_id"],
                        "status": r["status"],
                        "output": r.get("output", json.dumps(r, ensure_ascii=False)),
                    })
                api("POST", "/ingest", {
                    "protocol": "yqp/1.0", "message_type": "ingest",
                    "message_id": str(uuid.uuid4()),
                    "device_id": DEVICE_ID, "token": TOKEN,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "capability": "executor_result",
                    "schema_version": "v1",
                    "payload": {},
                    "command_results": results,
                })
        except Exception as e:
            print(f"[executor] Poll error: {e}")
        time.sleep(COMMAND_POLL_INTERVAL)


def goodbye():
    if TOKEN:
        try:
            api("POST", "/hello", {
                "hello_type": "goodbye", "device_id": DEVICE_ID,
                "token": TOKEN, "protocol": "yqp/1.0", "message_type": "hello",
            })
        except Exception:
            pass
    print("[executor] Shutdown complete")


def shutdown(*_):
    global running
    running = False
    goodbye()
    sys.exit(0)

def main():
    global running
    atexit.register(goodbye)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, shutdown)

    print(f"[executor] Gateway: {GATEWAY}")
    print(f"[executor] Device: {DEVICE_ID}")

    register()
    if TOKEN is None:
        sys.exit(1)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=command_loop, daemon=True).start()

    print("[executor] Running. Ctrl+C to stop.")
    while running:
        time.sleep(1)


if __name__ == "__main__":
    import json
    main()
