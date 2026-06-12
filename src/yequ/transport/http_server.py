"""HTTP transport — YQP + REST API + SSE streaming endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import os as _os_module

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, FileResponse
from starlette.routing import Route

from yequ.agent.core import AgentEvent
from yequ.protocol.messages import (
    HelloRegistration,
    HelloHeartbeat,
    Goodbye,
    Ingest,
    Ack,
    HelloResponse,
    RegistrationResponse,
    parse_hello,
)
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.storage.ingest import ingest_snapshot, ingest_metric
from yequ.storage.query import (
    get_latest_snapshot,
    query_snapshots_by_device,
    get_metrics,
    get_events,
)
from yequ.utils import now_iso

logger = logging.getLogger(__name__)

INITIAL_RETRIES = 10
INITIAL_INTERVAL = 30
MAX_INTERVAL = 600


def _compute_retry_after(retry_count: int) -> int:
    if retry_count < INITIAL_RETRIES:
        return INITIAL_INTERVAL
    exponent = retry_count - INITIAL_RETRIES
    return min(INITIAL_INTERVAL * (2 ** exponent), MAX_INTERVAL)


class GatewayApp:
    """Handles YQP protocol + REST API + SSE streaming."""

    def __init__(self, db_path, device_store, notify_router,
                 collector_runner=None, agent_config=None):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.collector = collector_runner
        self.agent_config = agent_config
        self._agent = None
        self._event_bus_ref = None  # set externally

    @property
    def agent(self):
        if self._agent is None and self.agent_config is not None:
            from yequ.agent.core import Agent
            self._agent = Agent(
                config=self.agent_config,
                db_path=self.db_path,
                data_dir=os.path.dirname(self.db_path),
            )
        return self._agent

    # ── YQP: Hello ───────────────────────────────────────────────

    async def handle_hello(self, request):
        try:
            raw = await request.body()
            body_str = raw.decode("utf-8")
            msg = parse_hello(body_str)
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        if isinstance(msg, HelloRegistration):
            return self._handle_registration(msg)
        elif isinstance(msg, HelloHeartbeat):
            return self._handle_heartbeat(msg, raw_body=body_str)
        elif isinstance(msg, Goodbye):
            return self._handle_goodbye(msg)

    def _handle_registration(self, msg):
        device = self.store.get_device(msg.device_id)
        if device:
            return JSONResponse(RegistrationResponse(
                status="approved", token=device.token,
                config={"collector": {"interval_seconds": 60}},
            ).to_dict())

        pending = self.store.get_pending_registration(msg.device_id)
        if pending is None:
            # Store device_info, capabilities, and actions in the pending record
            info = dict(msg.device_info)
            info["_capabilities"] = msg.capabilities
            info["_actions"] = getattr(msg, 'actions', [])
            self.store.add_pending_registration(msg.device_id, info)
            retry_count = 0
        else:
            retry_count = self.store.increment_retry(msg.device_id)

        retry_after = _compute_retry_after(retry_count)

        resp = HelloResponse(status="pending", retry_after=retry_after)
        if retry_count >= INITIAL_RETRIES + 10:
            resp.note = "请联系管理员审批"
        return JSONResponse(resp.to_dict())

    def _get_command_device(self, command_id: str) -> str | None:
        """Look up which device a command was sent to."""
        from yequ.storage.database import get_connection
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT device_id FROM pending_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return row["device_id"] if row else None

    def _process_command_results(self, raw_body: str | None) -> None:
        """If the request includes command_results, record them.
        Extract base64 images and save to media storage.
        """
        if not raw_body:
            return
        try:
            import json as _json
            data = _json.loads(raw_body)
            results = data.get("command_results", [])
            for r in results:
                cid = r.get("command_id")
                if not cid:
                    continue
                # Extract and save base64 image if present
                image_b64 = r.get("image_base64")
                image_url = None
                if image_b64:
                    from yequ.storage.media import save_media
                    mime = r.get("image_mime", "image/png")
                    # Look up device_id from the command record
                    dev_id = self._get_command_device(cid) or r.get("device_id", "unknown")
                    media_id = save_media(
                        os.path.dirname(self.db_path),
                        dev_id,
                        f"{cid}.png", image_b64, mime,
                    )
                    if media_id:
                        image_url = f"/api/media/{media_id}"
                # Store result (with image URL instead of base64 data)
                result = {k: v for k, v in r.items() if k != "image_base64"}
                if image_url:
                    result["image_url"] = image_url
                self.store.record_command_result(cid, _json.dumps(result, ensure_ascii=False))
        except Exception:
            pass

    def _publish_bus(self, event: dict):
        """Publish an event to the SSE bus if available."""
        if self._event_bus_ref is None:
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._event_bus_ref.publish(event))
        except RuntimeError:
            pass

    def _handle_goodbye(self, msg):
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        self.store.mark_offline(msg.device_id)

        from yequ.storage.ingest import ingest_event
        import time
        ingest_event(self.db_path, msg.device_id, "device_offline", "info",
                     f"设备主动下线: {msg.device_id}",
                     "设备发送了 goodbye 消息，正常关闭")
        self._publish_bus({
            "event_type": "device_offline", "severity": "info",
            "title": f"设备主动下线: {msg.device_id}",
            "body": "正常关闭", "device_id": msg.device_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

        return JSONResponse(Ack(message_id="goodbye", status="ok",
                                pending_commands=[]).to_dict())

    def _handle_heartbeat(self, msg, raw_body=None):
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        was_offline = not device.last_hello_at
        self.store.touch_hello(msg.device_id)

        # Process command results if included in heartbeat
        self._process_command_results(raw_body)

        if was_offline and not device.is_local:
            from yequ.storage.ingest import ingest_event
            ingest_event(self.db_path, msg.device_id, "device_online", "info",
                         f"设备恢复上线: {msg.device_id}", "心跳恢复")
            self._publish_bus({
                "event_type": "device_online", "severity": "info",
                "title": f"设备恢复上线: {msg.device_id}",
                "body": "心跳恢复", "device_id": msg.device_id,
                "timestamp": __import__('time').strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

        commands = self.store.dequeue_commands(msg.device_id)
        ack = Ack(message_id="heartbeat", status="ok",
                  pending_commands=[self._command_dict(c) for c in commands])
        return JSONResponse(ack.to_dict())

    # ── YQP: Ingest ──────────────────────────────────────────────

    async def handle_ingest(self, request):
        try:
            raw = await request.body()
            body_str = raw.decode("utf-8")
            msg = Ingest.from_json(body_str)
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        device = self.store.get_device_by_token(msg.token)
        if device is None:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        # Process command_results carried in ingest
        self._process_command_results(body_str)

        cap = self.store.get_capability(msg.device_id, msg.capability)
        data_type = cap.data_type if cap else "snapshot"

        if data_type == "snapshot":
            ingest_snapshot(self.db_path, msg.device_id, msg.capability,
                            msg.schema_version, msg.payload, timestamp=msg.timestamp)
        elif data_type == "metric":
            for key, value in msg.payload.items():
                if isinstance(value, (int, float)):
                    ingest_metric(self.db_path, msg.device_id, msg.capability,
                                  key, float(value), timestamp=msg.timestamp)

        commands = self.store.dequeue_commands(msg.device_id)
        ack = Ack(message_id=msg.message_id, status="ok",
                  pending_commands=[self._command_dict(c) for c in commands])
        return JSONResponse(ack.to_dict())

    # ── REST: Pending ────────────────────────────────────────────

    async def api_pending(self, request):
        pending = self.store.list_pending_registrations()
        return JSONResponse({
            "pending": [{
                "device_id": p["device_id"],
                "device_info": p["device_info"],
                "registered_at": p["registered_at"],
                "retry_count": p["retry_count"],
            } for p in pending],
            "total": len(pending),
        })

    async def api_pending_decline(self, request):
        device_id = request.path_params["device_id"]
        self.store.remove_pending_registration(device_id)
        return JSONResponse({"status": "declined", "device_id": device_id})

    # ── REST: Devices ────────────────────────────────────────────

    async def api_devices(self, request):
        devices = self.store.list_devices()
        return JSONResponse({"devices": [self._device_dict(d) for d in devices],
                             "total": len(devices)})

    async def api_device_detail(self, request):
        device = self.store.get_device(request.path_params["device_id"])
        if device is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        snaps = query_snapshots_by_device(self.db_path, device.device_id)
        snapshots = {}
        for s in snaps:
            snapshots[s["capability"]] = {
                "timestamp": s["timestamp"],
                "data": json.loads(s["payload_json"]),
            }
        caps = self.store.get_capabilities(device.device_id)
        capabilities = [{"name": c.name, "display": c.display,
                         "data_type": c.data_type, "interval": c.interval_seconds}
                        for c in caps]

        result = self._device_dict(device)
        result["snapshots"] = snapshots
        result["capabilities"] = capabilities
        return JSONResponse(result)

    async def api_device_approve(self, request):
        device_id = request.path_params["device_id"]
        pending = self.store.get_pending_registration(device_id)
        if pending is None:
            return JSONResponse({"error": "no pending registration for this device"},
                                status_code=404)

        # Derive reasonable labels from device_info
        info = pending.get("device_info", {})
        labels = {}
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

        device = self.store.register_device(
            device_id=device_id, labels=labels,
            source_type=info.get("source_type", "device"),
        )

        # Auto-create declared capabilities from the registration
        capabilities = pending.get("device_info", {}).get("_capabilities", [])
        for cap_decl in capabilities:
            if "name" not in cap_decl:
                continue
            self.store.add_capability(device_id, cap_decl)
            self.store.approve_capability(device_id, cap_decl["name"])

        # Auto-create declared actions from the registration
        actions = pending.get("device_info", {}).get("_actions", [])
        for act_decl in actions:
            if "name" not in act_decl:
                continue
            self.store.add_action(device_id, act_decl)
            self.store.approve_action(device_id, act_decl["name"])

        self.store.remove_pending_registration(device_id)

        from yequ.storage.ingest import ingest_event
        import time
        ingest_event(self.db_path, device_id, "device_approved", "info",
                     f"设备已批准: {device_id}",
                     f"labels={labels}, capabilities={len(capabilities)}")
        self._publish_bus({
            "event_type": "device_approved", "severity": "info",
            "title": f"设备已批准: {device_id}",
            "body": f"labels={labels}, {len(capabilities)} capabilities",
            "device_id": device_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

        return JSONResponse({
            "status": "approved", "device_id": device_id, "token": device.token,
            "capabilities_created": len(capabilities),
        })

    async def api_device_actions(self, request):
        device_id = request.path_params["device_id"]
        actions = self.store.get_actions(device_id)
        return JSONResponse({"device_id": device_id, "actions": actions})

    async def api_device_add_capability(self, request):
        """Allow an approved device to declare new capabilities post-registration."""
        device_id = request.path_params["device_id"]
        device = self.store.get_device(device_id)
        if device is None:
            return JSONResponse({"error": "device not found"}, status_code=404)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        name = body.get("name", "")
        if not name:
            return JSONResponse({"error": "capability name is required"}, status_code=400)

        self.store.add_capability(device_id, body)
        self.store.approve_capability(device_id, name)
        return JSONResponse({"status": "ok", "device_id": device_id, "capability": name})

    async def api_device_revoke(self, request):
        device_id = request.path_params["device_id"]
        self.store.revoke_device(device_id)

        from yequ.storage.ingest import ingest_event
        import time
        ingest_event(self.db_path, device_id, "device_revoked", "warning",
                     f"设备已撤销: {device_id}", "")
        self._publish_bus({
            "event_type": "device_revoked", "severity": "warning",
            "title": f"设备已撤销: {device_id}",
            "body": "", "device_id": device_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

        return JSONResponse({"status": "revoked", "device_id": device_id})

    # ── REST: Events ─────────────────────────────────────────────

    async def api_events(self, request):
        severity = request.query_params.get("severity")
        device_id = request.query_params.get("device_id")
        limit = int(request.query_params.get("limit", 50))
        events = get_events(self.db_path, device_id=device_id,
                            severity=severity, limit=limit)
        return JSONResponse({"events": [self._event_dict(e) for e in events],
                             "total": len(events)})

    # ── REST: Metrics ────────────────────────────────────────────

    async def api_metrics(self, request):
        device_id = request.path_params["device_id"]
        metric_name = request.path_params["metric_name"]
        limit = int(request.query_params.get("limit", 50))
        metrics = get_metrics(self.db_path, device_id, metric_name, limit=limit)
        return JSONResponse({
            "device_id": device_id,
            "metric_name": metric_name,
            "data_points": [
                {"timestamp": m["timestamp"], "value": m["value"], "unit": m["unit"]}
                for m in metrics
            ],
        })

    # ── REST: Monitor ────────────────────────────────────────────

    async def api_monitor(self, request):
        marker_path = os.path.join(os.path.dirname(self.db_path), "monitor_enabled")
        enabled = True
        if os.path.exists(marker_path):
            enabled = open(marker_path).read().strip() == "1"
        return JSONResponse({"monitor_enabled": enabled})

    async def api_monitor_toggle(self, request):
        action = request.path_params["action"]
        marker_path = os.path.join(os.path.dirname(self.db_path), "monitor_enabled")
        with open(marker_path, "w") as f:
            f.write("1" if action == "on" else "0")

        from yequ.storage.ingest import ingest_event
        import time
        ev_type = "monitor_enabled" if action == "on" else "monitor_disabled"
        ingest_event(self.db_path, "gateway", ev_type, "info",
                     f"巡检引擎已{'开启' if action == 'on' else '关闭'}", "")
        self._publish_bus({
            "event_type": ev_type, "severity": "info",
            "title": f"巡检引擎已{'开启' if action == 'on' else '关闭'}",
            "body": "", "device_id": "gateway",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

        return JSONResponse({"monitor_enabled": action == "on"})

    # ── REST: Media ──────────────────────────────────────────────

    async def serve_media(self, request):
        media_id = request.path_params["media_id"]
        from yequ.storage.media import get_media_path
        path = get_media_path(os.path.dirname(self.db_path), media_id)
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        import mimetypes
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(path, media_type=mime or "image/png")

    # ── REST: Audit ──────────────────────────────────────────────

    async def api_audit(self, request):
        from yequ.storage.audit import get_audit_log
        action = request.query_params.get("action")
        actor = request.query_params.get("actor")
        limit = int(request.query_params.get("limit", 50))
        entries = get_audit_log(self.db_path, action=action, actor=actor, limit=limit)
        return JSONResponse({"entries": entries, "total": len(entries)})

    # ── REST: Agent Config ────────────────────────────────────────

    async def api_agent_config_get(self, request):
        if self.agent_config is None:
            return JSONResponse({"error": "agent not configured"}, status_code=503)
        import os as _os
        env_map = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                    "openai": "OPENAI_API_KEY", "glm": "GLM_API_KEY"}
        env_key = env_map.get(self.agent_config.provider, "")
        return JSONResponse({
            "provider": self.agent_config.provider,
            "model": self.agent_config.model,
            "api_key_configured": bool(self.agent_config.api_key or _os.environ.get(env_key)),
        })

    async def api_agent_config_set(self, request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        provider = body.get("provider", "").strip()
        model = body.get("model", "").strip()
        api_key = body.get("api_key", "").strip()

        if provider:
            from yequ.agent.providers import get_preset
            preset = get_preset(provider)
            if preset is None:
                return JSONResponse({"error": f"Unknown provider: {provider}. Available: anthropic, deepseek, openai, glm, ollama, custom"}, status_code=400)
            self.agent_config.provider = provider
        if model:
            self.agent_config.model = model
        if api_key:
            self.agent_config.api_key = api_key

        # Persist to gateway.yaml
        self._save_agent_config()
        self._agent = None  # reset agent so new config takes effect

        import os as _os
        env_map = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                    "openai": "OPENAI_API_KEY", "glm": "GLM_API_KEY"}
        env_key = env_map.get(self.agent_config.provider, "")
        return JSONResponse({
            "provider": self.agent_config.provider,
            "model": self.agent_config.model,
            "api_key_configured": bool(self.agent_config.api_key or _os.environ.get(env_key)),
        })

    def _save_agent_config(self):
        """Persist agent config back to gateway.yaml."""
        import os as _os
        # __file__ = src/yequ/transport/http_server.py → 4 levels up = project root
        config_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
        yaml_path = _os.path.join(config_dir, "config", "gateway.yaml")
        try:
            import yaml
            with open(yaml_path) as f:
                raw = yaml.safe_load(f)
            raw.setdefault("agent", {})
            raw["agent"]["provider"] = self.agent_config.provider
            raw["agent"]["model"] = self.agent_config.model
            raw["agent"]["api_key"] = self.agent_config.api_key
            with open(yaml_path, "w") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.warning("Failed to persist agent config: %s", e)

    # ── REST: Agent ──────────────────────────────────────────────

    async def api_ask(self, request):
        if self.agent_config is None:
            return JSONResponse({"error": "agent not configured"}, status_code=503)
        try:
            body = await request.json()
            query = body.get("query", "")
        except Exception:
            return JSONResponse({"error": "invalid JSON, expected {query: ...}"},
                                status_code=400)

        answer = self.agent.ask(query)
        return JSONResponse({"answer": answer})

    # ── SSE: Agent Streaming ─────────────────────────────────────

    async def api_ask_stream(self, request):
        if self.agent_config is None:
            return StreamingResponse(
                self._sse_error("agent not configured"),
                media_type="text/event-stream")

        try:
            body = await request.json()
            query = body.get("query", "")
        except Exception:
            return StreamingResponse(
                self._sse_error("invalid JSON"),
                media_type="text/event-stream")

        agent = self.agent

        async def generate():
            import queue
            import threading

            q: queue.Queue = queue.Queue()
            loop = asyncio.get_event_loop()

            def run():
                try:
                    for event in agent.ask_stream(query):
                        q.put(event)
                except Exception as e:
                    q.put(AgentEvent(type="error", data=str(e)))
                q.put(None)  # sentinel

            threading.Thread(target=run, daemon=True).start()

            while True:
                try:
                    event = await loop.run_in_executor(None, q.get, True, 1.0)
                except queue.Empty:
                    continue
                if event is None:
                    return
                data = json.dumps(event.data, ensure_ascii=False) if event.data else "{}"
                yield f"event: {event.type}\ndata: {data}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ── SSE: Events Stream ───────────────────────────────────────

    async def api_events_stream(self, request):
        from yequ.events_bus import bus

        async def generate():
            q = await bus.subscribe()
            try:
                yield "event: connected\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=30)
                        data = json.dumps(event, ensure_ascii=False)
                        yield f"event: event\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(generate(), media_type="text/event-stream")

    async def _sse_error(self, msg):
        yield f"event: error\ndata: {json.dumps(msg)}\n\n"

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _device_dict(d):
        return {
            "device_id": d.device_id,
            "source_type": d.source_type,
            "status": d.display_status,
            "is_local": d.is_local,
            "labels": d.labels,
            "last_hello_at": d.last_hello_at,
            "created_at": d.created_at,
        }

    @staticmethod
    def _event_dict(e):
        return {
            "id": e["id"],
            "device_id": e["device_id"],
            "event_type": e["event_type"],
            "severity": e["severity"],
            "title": e["title"],
            "body": e["body"],
            "timestamp": e["timestamp"],
        }

    @staticmethod
    def _command_dict(c):
        return {
            "command_id": c["command_id"],
            "action": c["action"],
            "params": c["params"],
        }


# ── App Factory ──────────────────────────────────────────────────

def create_app(db_path, device_store, notify_router,
               collector_runner=None, agent_config=None, event_bus=None):
    gateway = GatewayApp(
        db_path=db_path, device_store=device_store,
        notify_router=notify_router, collector_runner=collector_runner,
        agent_config=agent_config,
    )
    gateway._event_bus_ref = event_bus
    dashboard_path = _os_module.path.join(_os_module.path.dirname(__file__), "..", "dashboard.html")

    async def dashboard(request):
        return FileResponse(dashboard_path)

    app = Starlette(routes=[
        # Dashboard
        Route("/", dashboard, methods=["GET"]),
        # YQP
        Route("/hello", gateway.handle_hello, methods=["POST"]),
        Route("/ingest", gateway.handle_ingest, methods=["POST"]),
        # Devices
        Route("/api/media/{media_id}", gateway.serve_media, methods=["GET"]),
        Route("/api/pending", gateway.api_pending, methods=["GET"]),
        Route("/api/pending/{device_id}", gateway.api_pending_decline, methods=["DELETE"]),
        Route("/api/devices", gateway.api_devices, methods=["GET"]),
        Route("/api/devices/{device_id}", gateway.api_device_detail, methods=["GET"]),
        Route("/api/devices/{device_id}/approve", gateway.api_device_approve, methods=["POST"]),
        Route("/api/devices/{device_id}/actions", gateway.api_device_actions, methods=["GET"]),
        Route("/api/devices/{device_id}/capabilities", gateway.api_device_add_capability,
              methods=["POST"]),
        Route("/api/devices/{device_id}", gateway.api_device_revoke, methods=["DELETE"]),
        # Events
        Route("/api/events", gateway.api_events, methods=["GET"]),
        # Metrics
        Route("/api/metrics/{device_id}/{metric_name}", gateway.api_metrics, methods=["GET"]),
        # Monitor
        Route("/api/monitor", gateway.api_monitor, methods=["GET"]),
        Route("/api/monitor/{action}", gateway.api_monitor_toggle, methods=["POST"]),
        # Audit
        Route("/api/audit", gateway.api_audit, methods=["GET"]),
        # Agent
        Route("/api/agent/config", gateway.api_agent_config_get, methods=["GET"]),
        Route("/api/agent/config", gateway.api_agent_config_set, methods=["POST"]),
        Route("/api/ask", gateway.api_ask, methods=["POST"]),
        Route("/api/ask/stream", gateway.api_ask_stream, methods=["POST"]),
        # SSE
        Route("/api/events/stream", gateway.api_events_stream, methods=["GET"]),
    ])
    return app
