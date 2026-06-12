"""Tool definitions for the YeQu Gateway Agent.

Tools are defined in Anthropic-compatible JSON Schema format,
with a handler function for each tool.
"""

from __future__ import annotations

import json
from typing import Any


# ── Tool Schema Definitions ──────────────────────────────────────────

TOOLS = [
    {
        "name": "list_devices",
        "description": "列出所有已注册的设备及在线状态",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_device_status",
        "description": "获取指定设备的最新系统指标（CPU、内存、磁盘、网络等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "设备ID，如 YeQuDesuDebian",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_events",
        "description": "获取最近的告警和事件记录",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                    "description": "按严重等级过滤",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认10",
                },
            },
        },
    },
    {
        "name": "get_metrics",
        "description": "获取指定设备的时序指标历史，用于趋势分析",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "设备ID",
                },
                "metric_name": {
                    "type": "string",
                    "description": "指标名，如 cpu_percent, memory_percent, disk_usage_percent",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认20",
                },
            },
            "required": ["device_id", "metric_name"],
        },
    },
    {
        "name": "get_monitor_status",
        "description": "查询巡检引擎的开关状态",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Tool Handler Functions ──────────────────────────────────────────

class ToolHandler:
    """Executes tool calls against the Gateway data."""

    def __init__(self, db_path: str, config_data_dir: str):
        self.db_path = db_path
        self.data_dir = config_data_dir

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name and return the result as a JSON string."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)
        try:
            result = handler(arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _tool_list_devices(self, _args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        devices = store.list_devices()

        result = []
        for d in devices:
            status = "online" if d.last_hello_at else ("local" if d.is_local else "unknown")
            result.append({
                "device_id": d.device_id,
                "status": status,
                "is_local": d.is_local,
                "labels": d.labels,
                "last_hello_at": d.last_hello_at,
            })
        return {"devices": result, "total": len(result)}

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
                "timestamp": s["timestamp"],
                "data": payload,
            }
        return result

    def _tool_get_events(self, args: dict) -> dict:
        from yequ.storage.query import get_events
        severity = args.get("severity")
        limit = args.get("limit", 10)

        events = get_events(self.db_path, severity=severity, limit=limit)
        return {
            "events": [
                {
                    "device_id": e["device_id"],
                    "type": e["event_type"],
                    "severity": e["severity"],
                    "title": e["title"],
                    "body": e["body"],
                    "timestamp": e["timestamp"],
                }
                for e in events
            ],
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
            "total": len(metrics),
        }

    def _tool_get_monitor_status(self, _args: dict) -> dict:
        import os
        marker_path = os.path.join(self.data_dir, "monitor_enabled")
        if os.path.exists(marker_path):
            state = open(marker_path).read().strip()
            enabled = state == "1"
        else:
            enabled = True  # default on
        return {
            "monitor_enabled": enabled,
            "note": "ON (all rules active)" if enabled else "OFF (keep-alive only)",
        }
