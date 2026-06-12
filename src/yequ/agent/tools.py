"""Tool definitions for the YeQu Gateway Agent.

Tools are defined in Anthropic-compatible JSON Schema format,
with a handler function for each tool.
"""

from __future__ import annotations

import json
import os
from typing import Any


TOOLS = [
    {
        "name": "list_devices",
        "description": "列出所有已注册设备及在线状态，包括 pending 待审批设备",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_device_status",
        "description": "获取指定设备的所有最新快照数据（系统指标、位置、服务列表等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_events",
        "description": "获取最近的告警和事件记录，可按严重等级过滤",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                "limit": {"type": "integer", "description": "返回条数，默认20"},
            },
        },
    },
    {
        "name": "get_metrics",
        "description": "获取指定设备的时序指标历史数据，用于趋势分析",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "metric_name": {"type": "string", "description": "如 cpu_percent, memory_percent, disk_usage_percent"},
                "limit": {"type": "integer", "description": "返回条数，默认20"},
            },
            "required": ["device_id", "metric_name"],
        },
    },
    {
        "name": "get_monitor_status",
        "description": "查询巡检引擎的开关状态",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── Management tools ──────────────────────────────────────────
    {
        "name": "approve_device",
        "description": "批准一个待注册的设备。调用前应先告知用户设备信息并获得确认。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "待批准的设备ID"},
                "labels": {
                    "type": "object",
                    "description": "设备的标签，如 {\"role\": \"phone\", \"owner\": \"yequdesu\"}",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "revoke_device",
        "description": "撤销一个已注册设备，使其无法再接入。需要用户明确确认。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "要撤销的设备ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "set_device_labels",
        "description": "更新设备的标签（如 role, owner, location 等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "labels": {"type": "object", "description": "要设置的标签键值对"},
            },
            "required": ["device_id", "labels"],
        },
    },
    {
        "name": "get_monitor_rules",
        "description": "获取当前配置的巡检规则列表",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_topology",
        "description": "读取网络拓扑文件内容（如果存在），了解网络结构和服务依赖",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_command",
        "description": "向指定设备下发指令。当前支持的操作：set_interval（修改采集间隔）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "目标设备ID"},
                "action": {"type": "string", "description": "指令动作，如 set_interval"},
                "params": {"type": "object", "description": "指令参数"},
            },
            "required": ["device_id", "action"],
        },
    },
]


class ToolHandler:
    """Executes tool calls against the Gateway data."""

    def __init__(self, db_path: str, config_data_dir: str, rules_path: str = "config/monitor_rules.yaml"):
        self.db_path = db_path
        self.data_dir = config_data_dir
        self.rules_path = rules_path

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)
        try:
            result = handler(arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── Query tools ──────────────────────────────────────────────

    def _tool_list_devices(self, _args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        devices = store.list_devices()
        return {
            "devices": [{
                "device_id": d.device_id,
                "status": d.display_status,
                "is_local": d.is_local,
                "labels": d.labels,
                "last_hello_at": d.last_hello_at,
            } for d in devices],
            "total": len(devices),
        }

    def _tool_get_device_status(self, args: dict) -> dict:
        from yequ.storage.query import query_snapshots_by_device
        device_id = args["device_id"]
        snaps = query_snapshots_by_device(self.db_path, device_id)
        if not snaps:
            return {"device_id": device_id, "error": "No data found"}
        result = {"device_id": device_id, "snapshots": {}}
        for s in snaps:
            payload = json.loads(s["payload_json"])
            result["snapshots"][s["capability"]] = {
                "timestamp": s["timestamp"], "data": payload,
            }
        return result

    def _tool_get_events(self, args: dict) -> dict:
        from yequ.storage.query import get_events
        severity = args.get("severity")
        limit = args.get("limit", 20)
        events = get_events(self.db_path, severity=severity, limit=limit)
        return {
            "events": [{
                "device_id": e["device_id"],
                "type": e["event_type"],
                "severity": e["severity"],
                "title": e["title"],
                "body": e["body"],
                "timestamp": e["timestamp"],
            } for e in events],
            "total": len(events),
        }

    def _tool_get_metrics(self, args: dict) -> dict:
        from yequ.storage.query import get_metrics
        device_id = args["device_id"]
        metric_name = args["metric_name"]
        limit = args.get("limit", 20)
        metrics = get_metrics(self.db_path, device_id, metric_name, limit=limit)
        return {
            "device_id": device_id,
            "metric_name": metric_name,
            "data_points": [
                {"timestamp": m["timestamp"], "value": m["value"], "unit": m["unit"]}
                for m in metrics
            ],
        }

    def _tool_get_monitor_status(self, _args: dict) -> dict:
        marker_path = os.path.join(self.data_dir, "monitor_enabled")
        if os.path.exists(marker_path):
            enabled = open(marker_path).read().strip() == "1"
        else:
            enabled = True
        return {
            "monitor_enabled": enabled,
            "note": "ON (all rules active)" if enabled else "OFF (keep-alive only)",
        }

    # ── Management tools ─────────────────────────────────────────

    def _tool_approve_device(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        labels = args.get("labels") or {}

        pending = store.get_pending_registration(device_id)
        if pending is None:
            return {"error": f"No pending registration for device: {device_id}"}

        # If no labels provided, derive from device_info
        if not labels:
            info = pending.get("device_info", {})
            os_name = (info.get("os") or "").lower()
            if "windows" in os_name:
                labels["role"] = "desktop"
            elif "android" in os_name:
                labels["role"] = "phone"
            elif "linux" in os_name:
                labels["role"] = "server"
            else:
                labels["role"] = "device"
            if info.get("hostname"):
                labels["hostname"] = info["hostname"]

        device = store.register_device(device_id=device_id, labels=labels)

        # Auto-create declared capabilities
        capabilities = pending.get("device_info", {}).get("_capabilities", [])
        created = 0
        for cap_decl in capabilities:
            if "name" not in cap_decl:
                continue
            store.add_capability(device_id, cap_decl)
            store.approve_capability(device_id, cap_decl["name"])
            created += 1

        store.remove_pending_registration(device_id)
        return {
            "status": "approved",
            "device_id": device_id,
            "token": device.token,
            "capabilities_created": created,
        }

    def _tool_revoke_device(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        device = store.get_device(device_id)
        if device is None:
            return {"error": f"Device not found: {device_id}"}
        store.revoke_device(device_id)
        return {"status": "revoked", "device_id": device_id}

    def _tool_set_device_labels(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        labels = args["labels"]
        store.update_labels(device_id, labels)
        return {"status": "ok", "device_id": device_id, "labels": labels}

    def _tool_get_monitor_rules(self, _args: dict) -> dict:
        if not os.path.exists(self.rules_path):
            return {"rules": [], "note": "No rules file found"}
        from yequ.monitor.rules import load_rules_from_yaml
        rules = load_rules_from_yaml(self.rules_path)
        return {
            "rules": [{
                "name": r.name,
                "description": r.description,
                "condition_type": r.condition_type,
                "condition_params": r.condition_params,
                "severity": r.severity,
                "cooldown_seconds": r.cooldown_seconds,
            } for r in rules],
        }

    def _tool_get_topology(self, _args: dict) -> dict:
        # Check standard locations for the topology file
        candidates = [
            os.path.join(self.data_dir, "network-topology.md"),
            "file-share/data/network-topology.md",
            os.path.expanduser("~/YeQu-gateway/file-share/data/network-topology.md"),
        ]
        for path in candidates:
            if os.path.exists(path):
                content = open(path).read()
                return {"path": path, "content": content}
        return {"error": "Network topology file not found", "checked_paths": candidates}

    def _tool_send_command(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        action = args["action"]
        params = args.get("params", {})

        device = store.get_device(device_id)
        if device is None:
            return {"error": f"Device not found: {device_id}"}

        supported = {"set_interval", "restart_collector", "ping"}
        if action not in supported:
            return {"error": f"Unknown action: {action}. Supported: {', '.join(sorted(supported))}"}

        command_id = store.enqueue_command(device_id, action, params)
        return {
            "status": "queued",
            "device_id": device_id,
            "command_id": command_id,
            "action": action,
            "params": params,
            "note": "Command will be delivered on device's next heartbeat or ingest Ack",
        }
