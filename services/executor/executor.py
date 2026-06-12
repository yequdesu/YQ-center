#!/usr/bin/env python3
"""YeQu Executor Service — 本机指令执行器。

独立进程，通过 YQP 协议接入 Gateway。与 Core 完全对等解耦。
注册 → 声明 actions → 轮询指令 → 执行 → 上报。
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
TOKEN = None
running = True

ACTIONS = [
    {
        "name": "run_diagnostics",
        "display": "运行诊断",
        "description": "执行系统诊断（system/network/disk/memory）",
        "params": {
            "target": {
                "type": "string",
                "enum": ["system", "network", "disk", "memory"],
            }
        },
    },
    {
        "name": "check_service",
        "display": "检查服务状态",
        "description": "查询 systemd 服务是否运行中",
        "params": {"service_name": {"type": "string"}},
    },
    {
        "name": "read_log",
        "display": "读取日志",
        "description": "读取指定文件最后 N 行",
        "params": {
            "path": {"type": "string"},
            "lines": {"type": "integer"},
        },
    },
]

ALLOWED_LOG_PATHS = {
    "/var/log/syslog",
    "/var/log/messages",
    "/var/log/dmesg",
    os.path.expanduser("~/.local/share/yequ-gateway/gateway.log"),
    "/var/log/nginx/access.log",
    "/var/log/nginx/error.log",
}


def execute(action, params):
    try:
        if action == "run_diagnostics":
            return _diagnostics(params.get("target", "system"))
        elif action == "check_service":
            return _check_service(params.get("service_name", ""))
        elif action == "read_log":
            return _read_log(params.get("path", ""), params.get("lines", 50))
        elif action in ("ping", "set_interval", "restart_collector"):
            return {"status": "ok", "output": action}
        else:
            return {"status": "error", "output": f"Unknown action: {action}"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def _diagnostics(target):
    cmds = {
        "system": (["uptime"], "系统概览"),
        "network": (["ss", "-tlnp"], "监听端口"),
        "disk": (["df", "-h", "/"], "磁盘使用"),
        "memory": (["free", "-h"], "内存使用"),
    }
    entry = cmds.get(target)
    if not entry:
        return {"status": "error", "output": f"Unknown target: {target}"}
    r = subprocess.run(entry[0], capture_output=True, text=True, timeout=10)
    return {
        "status": "ok",
        "output": r.stdout[:4096] or r.stderr[:1024],
    }


def _check_service(name):
    if not name or not all(c.isalnum() or c in "-_." for c in name):
        return {"status": "error", "output": "Invalid service name"}
    r = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True, text=True, timeout=5,
    )
    return {"status": "ok", "output": f"Service {name}: {r.stdout.strip()}"}


def _read_log(path, lines):
    if not path:
        return {"status": "error", "output": "path is required"}
    if path not in ALLOWED_LOG_PATHS and not path.startswith("/var/log/"):
        return {"status": "error", "output": f"Path not allowed: {path}"}
    if not os.path.exists(path):
        return {"status": "error", "output": f"File not found: {path}"}
    n = max(1, min(int(lines), 500))
    r = subprocess.run(["tail", "-n", str(n), path], capture_output=True, text=True, timeout=5)
    return {"status": "ok", "output": r.stdout[:8192]}


def api(method, path, data=None):
    url = f"{GATEWAY}{path}"
    try:
        if method == "GET":
            return requests.get(url, params=data, timeout=10).json()
        else:
            return requests.post(url, json=data, timeout=10).json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


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
            "actions": ACTIONS,
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
    print("[executor] Registration timed out")


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
                    print(f"[executor] {cmd['action']}")
                    r = execute(cmd["action"], cmd.get("params", {}))
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


def main():
    global running
    atexit.register(goodbye)
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], 'running', False))
    signal.signal(signal.SIGTERM, lambda *_: setattr(sys.modules[__name__], 'running', False))

    print(f"[executor] Gateway: {GATEWAY}")
    print(f"[executor] Device ID: {DEVICE_ID}")

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
