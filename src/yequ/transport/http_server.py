"""HTTP transport — YQP + REST API + SSE streaming endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from yequ.protocol.messages import (
    HelloRegistration,
    HelloHeartbeat,
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

    # ── YQP: Hello ───────────────────────────────────────────────

    async def handle_hello(self, request):
        try:
            body = await request.body()
            msg = parse_hello(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        if isinstance(msg, HelloRegistration):
            return self._handle_registration(msg)
        elif isinstance(msg, HelloHeartbeat):
            return self._handle_heartbeat(msg)

    def _handle_registration(self, msg):
        device = self.store.get_device(msg.device_id)
        if device:
            return JSONResponse(RegistrationResponse(
                status="approved", token=device.token,
                config={"collector": {"interval_seconds": 60}},
            ).to_dict())

        pending = self.store.get_pending_registration(msg.device_id)
        if pending is None:
            # Store both device_info and capabilities in the pending record
            info = dict(msg.device_info)
            info["_capabilities"] = msg.capabilities
            self.store.add_pending_registration(msg.device_id, info)
            retry_count = 0
        else:
            retry_count = self.store.increment_retry(msg.device_id)

        retry_after = _compute_retry_after(retry_count)

        resp = HelloResponse(status="pending", retry_after=retry_after)
        if retry_count >= INITIAL_RETRIES + 10:
            resp.note = "请联系管理员审批"
        return JSONResponse(resp.to_dict())

    def _handle_heartbeat(self, msg):
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        self.store.touch_hello(msg.device_id)
        commands = self.store.dequeue_commands(msg.device_id)
        ack = Ack(message_id="heartbeat", status="ok",
                  pending_commands=[self._command_dict(c) for c in commands])
        return JSONResponse(ack.to_dict())

    # ── YQP: Ingest ──────────────────────────────────────────────

    async def handle_ingest(self, request):
        try:
            body = await request.body()
            msg = Ingest.from_json(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        device = self.store.get_device_by_token(msg.token)
        if device is None:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

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

        device = self.store.register_device(
            device_id=device_id, labels={"role": "pending_approval"},
        )

        # Auto-create declared capabilities from the registration
        capabilities = pending.get("device_info", {}).get("_capabilities", [])
        for cap_decl in capabilities:
            if "name" not in cap_decl:
                continue
            self.store.add_capability(device_id, cap_decl)
            self.store.approve_capability(device_id, cap_decl["name"])

        self.store.remove_pending_registration(device_id)
        return JSONResponse({
            "status": "approved", "device_id": device_id, "token": device.token,
            "capabilities_created": len(capabilities),
        })

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
        return JSONResponse({"monitor_enabled": action == "on"})

    # ── REST: Audit ──────────────────────────────────────────────

    async def api_audit(self, request):
        from yequ.storage.audit import get_audit_log
        action = request.query_params.get("action")
        actor = request.query_params.get("actor")
        limit = int(request.query_params.get("limit", 50))
        entries = get_audit_log(self.db_path, action=action, actor=actor, limit=limit)
        return JSONResponse({"entries": entries, "total": len(entries)})

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

        from yequ.agent.core import create_agent
        agent = create_agent(config=self.agent_config, db_path=self.db_path,
                             data_dir=os.path.dirname(self.db_path))
        answer = agent.ask(query)
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

        from yequ.agent.core import create_agent
        agent = create_agent(config=self.agent_config, db_path=self.db_path,
                             data_dir=os.path.dirname(self.db_path))

        async def generate():
            for event in agent.ask_stream(query):
                data = json.dumps(event.data, ensure_ascii=False) if event.data else "{}"
                yield f"event: {event.type}\ndata: {data}\n\n"
                await asyncio.sleep(0)

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
               collector_runner=None, agent_config=None):
    gateway = GatewayApp(
        db_path=db_path, device_store=device_store,
        notify_router=notify_router, collector_runner=collector_runner,
        agent_config=agent_config,
    )
    app = Starlette(routes=[
        # YQP
        Route("/hello", gateway.handle_hello, methods=["POST"]),
        Route("/ingest", gateway.handle_ingest, methods=["POST"]),
        # Devices
        Route("/api/devices", gateway.api_devices, methods=["GET"]),
        Route("/api/devices/{device_id}", gateway.api_device_detail, methods=["GET"]),
        Route("/api/devices/{device_id}/approve", gateway.api_device_approve, methods=["POST"]),
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
        Route("/api/ask", gateway.api_ask, methods=["POST"]),
        Route("/api/ask/stream", gateway.api_ask_stream, methods=["POST"]),
        # SSE
        Route("/api/events/stream", gateway.api_events_stream, methods=["GET"]),
    ])
    return app
