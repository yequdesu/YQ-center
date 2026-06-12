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
        "description": "列出所有已注册设备及在线状态",
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
        "name": "list_pending",
        "description": "列出所有等待审批的设备注册请求。用户说'有没有待审批'或'批准'时先调这个。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "approve_device",
        "description": "批准一个待注册的设备。直接调用即可，无需再次确认。",
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
        "description": "撤销一个已注册设备。仅在用户明确说撤销/删除/移除某设备时调用。",
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
        "description": "更新设备的标签和/或类型。type可选值：device/service/gateway。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "labels": {"type": "object", "description": "要设置的标签键值对"},
                "source_type": {"type": "string", "description": "设备类型：device/service/gateway"},
            },
            "required": ["device_id"],
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
        "name": "check_device_online",
        "description": "主动检查设备是否在线。根据最近心跳时间判断：fresh=在线，stale=可能离线，gone=确定离线。比等巡检告警更快。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "要检查的设备ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "list_device_actions",
        "description": "查询指定设备支持哪些操作指令（action）。返回设备声明过的可执行操作列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "check_command_result",
        "description": "查询之前下发的指令的执行结果。用 command_id 查询。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command_id": {"type": "string", "description": "指令ID"},
            },
            "required": ["command_id"],
        },
    },
    {
        "name": "send_command",
        "description": "向设备下发指令。先用 list_device_actions 查看设备支持哪些操作再调用。指令发出后，设备在下一次心跳时收到并执行，执行结果在下下次心跳时返回。建议告知用户稍后查询结果。",
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

    def _tool_list_pending(self, _args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        pending = store.list_pending_registrations()
        return {
            "pending": [{
                "device_id": p["device_id"],
                "device_info": p["device_info"],
                "registered_at": p["registered_at"],
                "retry_count": p["retry_count"],
            } for p in pending],
            "total": len(pending),
        }

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
            elif "android" in os_name or "ios" in os_name:
                labels["role"] = "phone"
            elif "mac" in os_name or "darwin" in os_name:
                labels["role"] = "desktop"
            elif "linux" in os_name:
                labels["role"] = "server"
            else:
                labels["role"] = "device"
            if info.get("hostname"):
                labels["hostname"] = info["hostname"]

        device = store.register_device(device_id=device_id, labels=labels)
        store.touch_hello(device_id)  # mark online immediately

        # Auto-create declared capabilities
        capabilities = pending.get("device_info", {}).get("_capabilities", [])
        created = 0
        for cap_decl in capabilities:
            if "name" not in cap_decl:
                continue
            store.add_capability(device_id, cap_decl)
            store.approve_capability(device_id, cap_decl["name"])
            created += 1

        # Auto-create declared actions
        actions = pending.get("device_info", {}).get("_actions", [])
        store.clear_actions(device_id)
        for act_decl in actions:
            if "name" not in act_decl:
                continue
            store.add_action(device_id, act_decl)
            store.approve_action(device_id, act_decl["name"])

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
        labels = args.get("labels") or {}
        source_type = args.get("source_type") or ""
        if labels:
            store.update_labels(device_id, labels)
        if source_type:
            store.update_source_type(device_id, source_type)
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

    def _tool_check_device_online(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        from datetime import datetime, timezone, timedelta
        import time as _time

        store = DeviceStore(self.db_path)
        device = store.get_device(args["device_id"])
        if device is None:
            return {"device_id": args["device_id"], "error": "device not found"}

        # Fast path: heartbeat is fresh → definitely online
        if device.last_hello_at:
            ts = device.last_hello_at.replace("Z", "+00:00")
            last = datetime.fromisoformat(ts)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if device.source_type in ("service", "gateway"):
                expected = 15
            else:
                caps = store.get_capabilities(args["device_id"])
                intervals = [c.interval_seconds for c in caps] or [60]
                expected = min(min(intervals), 300)
            if age < expected * 2:
                return {
                    "device_id": args["device_id"], "status": "online",
                    "method": "heartbeat",
                    "last_heartbeat_age_seconds": int(age),
                    "note": f"心跳正常，{int(age)}秒前",
                }

        # Slow path: send a ping via the command channel, wait for reply
        if device.is_local or device.source_type in ("service", "gateway"):
            cmd_id = store.enqueue_command(args["device_id"], "ping", {})
            # Poll for result — local services poll every 3s
            for attempt in range(6):
                _time.sleep(2)
                result = self._tool_check_command_result({"command_id": cmd_id})
                if result.get("result"):
                    return {
                        "device_id": args["device_id"], "status": "online",
                        "method": "active_ping",
                        "ping_roundtrip_attempts": attempt + 1,
                        "note": f"主动 ping 成功，{ (attempt+1)*2 }秒内响应",
                    }
            return {
                "device_id": args["device_id"], "status": "offline",
                "method": "active_ping",
                "note": "主动 ping 无响应，设备可能已离线",
            }

        # Remote device: just report what we know
        if device.last_hello_at:
            return {
                "device_id": args["device_id"], "status": "stale",
                "method": "heartbeat",
                "note": f"心跳延迟，无法主动 ping（设备在 NAT 后）",
            }
        return {
            "device_id": args["device_id"], "status": "unknown",
            "note": "从未收到心跳",
        }

    def _tool_check_command_result(self, args: dict) -> dict:
        from yequ.storage.database import get_connection
        command_id = args["command_id"]
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM pending_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            return {"error": f"Command not found: {command_id}"}
        r = dict(row)
        result = None
        if r.get("result_json"):
            result = json.loads(r["result_json"])
        # If result has image_url, convert to markdown so Agent passes it through
        image_md = ""
        if result and result.get("image_url"):
            image_md = f"![screenshot]({result['image_url']})"
        return {
            "command_id": command_id,
            "device_id": r["device_id"],
            "action": r["action"],
            "delivered": bool(r["delivered"]),
            "result": result,
            "image_markdown": image_md,
            "_note": "If image_markdown is non-empty, include it verbatim in your reply so the user sees the image.",
        }

    def _tool_list_device_actions(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        actions = store.get_actions(device_id)
        return {"device_id": device_id, "actions": actions}

    def _tool_send_command(self, args: dict) -> dict:
        from yequ.registry.store import DeviceStore
        store = DeviceStore(self.db_path)
        device_id = args["device_id"]
        action = args["action"]
        params = args.get("params", {})

        device = store.get_device(device_id)
        if device is None:
            return {"error": f"Device not found: {device_id}"}

        # Pre-check: don't send commands to offline devices
        if not device.is_local:
            online = self._tool_check_device_online({"device_id": device_id})
            status = online.get("status", "unknown")
            if status == "offline":
                return {"error": f"Device {device_id} is offline. Command rejected."}
            if status == "unknown":
                return {"error": f"Device {device_id} has never been online. Command rejected."}
            if status == "stale":
                pass  # Warn but allow

        # Blocked commands list
        BLOCKED = {"shutdown", "reboot", "format", "rm", "delete_all",
                   "set_gateway_config", "access_other_device"}
        if action in BLOCKED:
            from yequ.message_queue import mq
            mq.publish("yequ:events", {
                "event_type": "action_blocked", "severity": "warning",
                "title": f"禁止指令被调用: {action}",
                "device_id": device_id,
            })
            return {"error": f"Action '{action}' is blocked"}

        # Check if the device has declared this action
        declared = {a["name"]: a for a in store.get_actions(device_id)}
        builtin = {"set_interval", "restart_collector", "ping"}
        if action not in declared and action not in builtin:
            return {"error": f"Unknown action: {action}. Device supports: {', '.join(sorted(declared.keys() | builtin))}"}

        # Auto-adapt legacy params to declared schema
        if action in declared:
            declared_params = declared[action].get("params", {})
            if "command" in declared_params and "command" not in params:
                # Agent sent {script, args} — convert to {command}
                if params.get("script"):
                    cmd = params["script"]
                    if params.get("args"):
                        cmd += " " + " ".join(str(a) for a in params["args"])
                    params = {"command": cmd}

        command_id = store.enqueue_command(device_id, action, params)

        # For local services, wait for the result immediately (they poll every 3s)
        if device.source_type in ("service", "gateway") or device.is_local:
            import time as _time
            for _ in range(6):
                _time.sleep(2)
                r = self._tool_check_command_result({"command_id": command_id})
                if r.get("result"):
                    return {
                        "status": "completed",
                        "device_id": device_id,
                        "command_id": command_id,
                        "action": action,
                        "result": r["result"],
                        "note": "Executed and result received immediately",
                    }

        return {
            "status": "queued",
            "device_id": device_id,
            "command_id": command_id,
            "action": action,
            "params": params,
            "note": "Command queued. DO NOT call check_command_result repeatedly — the result will arrive asynchronously. Tell the user the command ID and that they will be notified when complete.",
        }
