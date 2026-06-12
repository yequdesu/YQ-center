#!/usr/bin/env python3
"""YeQu Executor Service — 本机命令执行器。

Agent 下发 exec 指令 → 本机执行 shell 命令 → 返回输出。
通过 YQP 协议接入 Gateway，与 Core 完全解耦。
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
HEARTBEAT_INTERVAL = 10
COMMAND_POLL_INTERVAL = 3
COMMAND_TIMEOUT = 30
TOKEN = None
running = True


def execute(action, params):
    """Execute an action. 'exec' runs a shell command directly."""
    try:
        if action == "exec":
            cmd = params.get("command", "")
            if not cmd:
                return {"status": "error", "output": "command is required"}
            timeout = min(params.get("timeout", COMMAND_TIMEOUT), 120)
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            )
            return {
                "status": "ok" if r.returncode == 0 else "error",
                "output": (r.stdout + r.stderr)[:16384],
                "exit_code": r.returncode,
            }
        elif action in ("ping", "set_interval", "restart_collector"):
            return {"status": "ok", "output": action}
        else:
            return {"status": "error", "output": f"Unknown action: {action}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": f"Command timed out"}
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
            "actions": [{
                "name": "exec",
                "display": "执行命令",
                "description": "在当前机器上执行任意 shell 命令并返回输出。Agent 可直接下发 docker ps、systemctl status 等。",
                "params": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认30，最大120"},
                },
            }],
        })
        if resp.get("status") == "approved":
            TOKEN = resp["token"]
            print(f"[executor] Approved. Token: {TOKEN[:16]}...")
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
                    print(f"[executor] {action}: {params.get('command', params)}")
                    r = execute(action, params)
                    results.append({
                        "command_id": cmd["command_id"],
                        "status": r["status"],
                        "output": r.get("output", ""),
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
    main()
