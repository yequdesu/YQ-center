"""HTTP transport — YQP + REST API + SSE streaming endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

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
    parse_ingest,
)
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Registration Backoff ─────────────────────────────────────────────

INITIAL_RETRIES = 10
INITIAL_INTERVAL = 30
MAX_INTERVAL = 600


def _compute_retry_after(retry_count: int) -> int:
    if retry_count < INITIAL_RETRIES:
        return INITIAL_INTERVAL
    exponent = retry_count - INITIAL_RETRIES
    interval = INITIAL_INTERVAL * (2 ** exponent)
    return min(interval, MAX_INTERVAL)


# ── Gateway App ──────────────────────────────────────────────────────

class GatewayApp:
    """Handles YQP protocol + REST API + SSE streaming."""

    def __init__(self, db_path: str, device_store: DeviceStore,
                 notify_router: NotifyRouter, collector_runner=None,
                 agent_config=None):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.collector = collector_runner
        self.agent_config = agent_config

    # ── YQP: Hello ───────────────────────────────────────────────

    async def handle_hello(self, request: Request) -> JSONResponse:
        try:
            body = await request.body()
            msg = parse_hello(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        if isinstance(msg, HelloRegistration):
            return self._handle_registration_hello(msg)
        elif isinstance(msg, HelloHeartbeat):
            return self._handle_heartbeat_hello(msg)

    def _handle_registration_hello(self, msg: HelloRegistration) -> JSONResponse:
        device = self.store.get_device(msg.device_id)
        if device:
            return JSONResponse(RegistrationResponse(
                status="approved", token=device.token,
                config={"collector": {"interval_seconds": 60}},
            ).to_dict())

        pending = self.store.get_pending_registration(msg.device_id)
        if pending is None:
            self.store.add_pending_registration(msg.device_id, msg.device_info)
            retry_count = 0
        else:
            retry_count = self.store.increment_retry(msg.device_id)

        retry_after = _compute_retry_after(retry_count)

        if retry_count >= INITIAL_RETRIES + 10:
            return JSONResponse({
                "status": "pending", "retry_after": retry_after,
                "note": "请联系管理员审批",
            })

        return JSONResponse(HelloResponse(status="pending", retry_after=retry_after).to_dict())

    def _handle_heartbeat_hello(self, msg: HelloHeartbeat) -> JSONResponse:
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        self.store.touch_hello(msg.device_id)
        return JSONResponse(Ack(message_id="", status="ok", pending_commands=[]).to_dict())

    # ── YQP: Ingest ──────────────────────────────────────────────

    async def handle_ingest(self, request: Request) -> JSONResponse:
        try:
            body = await request.body()
            msg = parse_ingest(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        device = self.store.get_device_by_token(msg.token)
        if device is None:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        from yequ.storage.ingest import ingest_snapshot, ingest_metric

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

        return JSONResponse(Ack(message_id=msg.message_id, status="ok").to_dict())

    # ── REST: Devices ────────────────────────────────────────────

    async def api_devices(self, request: Request) -> JSONResponse:
        devices = self.store.list_devices()
        result = []
        for d in devices:
            result.append({
                "device_id": d.device_id,
                "status": "online" if d.last_hello_at else ("local" if d.is_local else "unknown"),
                "is_local": d.is_local,
                "labels": d.labels,
                "last_hello_at": d.last_hello_at,
                "created_at": d.created_at,
            })
        return JSONResponse({"devices": result, "total": len(result)})

    async def api_device_detail(self, request: Request) -> JSONResponse:
        device_id = request.path_params["device_id"]
        device = self.store.get_device(device_id)
        if device is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        from yequ.storage.query import query_snapshots_by_device, get_metrics
        snaps = query_snapshots_by_device(self.db_path, device_id)

        snapshots = {}
        for s in snaps:
            snapshots[s["capability"]] = {
                "timestamp": s["timestamp"],
                "data": json.loads(s["payload_json"]),
            }

        caps = self.store.get_capabilities(device_id)
        capabilities = [{"name": c.name, "display": c.display,
                         "data_type": c.data_type, "interval": c.interval_seconds} for c in caps]

        return JSONResponse({
            "device_id": device.device_id,
            "status": "online" if device.last_hello_at else ("local" if device.is_local else "unknown"),
            "is_local": device.is_local,
            "labels": device.labels,
            "last_hello_at": device.last_hello_at,
            "snapshots": snapshots,
            "capabilities": capabilities,
        })

    async def api_device_approve(self, request: Request) -> JSONResponse:
        device_id = request.path_params["device_id"]
        pending = self.store.get_pending_registration(device_id)
        if pending is None:
            return JSONResponse({"error": "no pending registration for this device"}, status_code=404)

        device = self.store.register_device(
            device_id=device_id,
            labels={"role": "pending_approval"},
        )
        self.store.remove_pending_registration(device_id)
        return JSONResponse({"status": "approved", "device_id": device_id, "token": device.token})

    async def api_device_revoke(self, request: Request) -> JSONResponse:
        device_id = request.path_params["device_id"]
        self.store.revoke_device(device_id)
        return JSONResponse({"status": "revoked", "device_id": device_id})

    # ── REST: Events ─────────────────────────────────────────────

    async def api_events(self, request: Request) -> JSONResponse:
        from yequ.storage.query import get_events

        severity = request.query_params.get("severity")
        device_id = request.query_params.get("device_id")
        limit = int(request.query_params.get("limit", 50))

        events = get_events(self.db_path, device_id=device_id, severity=severity, limit=limit)
        return JSONResponse({
            "events": [{
                "id": e["id"],
                "device_id": e["device_id"],
                "event_type": e["event_type"],
                "severity": e["severity"],
                "title": e["title"],
                "body": e["body"],
                "timestamp": e["timestamp"],
            } for e in events],
            "total": len(events),
        })

    # ── REST: Metrics ────────────────────────────────────────────

    async def api_metrics(self, request: Request) -> JSONResponse:
        from yequ.storage.query import get_metrics

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

    async def api_monitor(self, request: Request) -> JSONResponse:
        marker_path = os.path.join(
            os.path.dirname(self.db_path), "monitor_enabled"
        )
        enabled = True
        if os.path.exists(marker_path):
            enabled = open(marker_path).read().strip() == "1"
        return JSONResponse({"monitor_enabled": enabled})

    async def api_monitor_toggle(self, request: Request) -> JSONResponse:
        action = request.path_params["action"]  # "on" or "off"
        marker_path = os.path.join(
            os.path.dirname(self.db_path), "monitor_enabled"
        )
        with open(marker_path, "w") as f:
            f.write("1" if action == "on" else "0")
        return JSONResponse({"monitor_enabled": action == "on"})

    # ── REST: Agent Ask ──────────────────────────────────────────

    async def api_ask(self, request: Request) -> JSONResponse:
        if self.agent_config is None:
            return JSONResponse({"error": "agent not configured"}, status_code=503)

        try:
            body = await request.json()
            query = body.get("query", "")
        except Exception:
            return JSONResponse({"error": "invalid JSON, expected {query: ...}"}, status_code=400)

        from yequ.agent.core import create_agent
        agent = create_agent(
            config=self.agent_config,
            db_path=self.db_path,
            data_dir=os.path.dirname(self.db_path),
        )
        answer = agent.ask(query)
        return JSONResponse({"answer": answer})

    # ── SSE: Agent Ask Stream ────────────────────────────────────

    async def api_ask_stream(self, request: Request) -> StreamingResponse:
        if self.agent_config is None:
            return StreamingResponse(
                self._sse_error("agent not configured"),
                media_type="text/event-stream",
            )

        try:
            body = await request.json()
            query = body.get("query", "")
        except Exception:
            return StreamingResponse(
                self._sse_error("invalid JSON"),
                media_type="text/event-stream",
            )

        from yequ.agent.core import create_agent
        agent = create_agent(
            config=self.agent_config,
            db_path=self.db_path,
            data_dir=os.path.dirname(self.db_path),
        )

        async def generate():
            for event in agent.ask_stream(query):
                data = json.dumps(event.data, ensure_ascii=False) if event.data else "{}"
                yield f"event: {event.type}\ndata: {data}\n\n"
                await asyncio.sleep(0)

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ── SSE: Events Stream ───────────────────────────────────────

    async def api_events_stream(self, request: Request) -> StreamingResponse:
        from yequ.events_bus import bus

        async def generate():
            q = await bus.subscribe()
            try:
                # Send initial connected event
                yield f"event: connected\ndata: {{}}\n\n"
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

    async def _sse_error(self, msg: str):
        yield f"event: error\ndata: {json.dumps(msg)}\n\n"


# ── App Factory ──────────────────────────────────────────────────────

def create_app(db_path: str, device_store: DeviceStore,
               notify_router: NotifyRouter, collector_runner=None,
               agent_config=None) -> Starlette:
    """Create the Starlette app with all routes."""
    gateway = GatewayApp(
        db_path=db_path, device_store=device_store,
        notify_router=notify_router, collector_runner=collector_runner,
        agent_config=agent_config,
    )

    app = Starlette(routes=[
        # YQP
        Route("/hello", gateway.handle_hello, methods=["POST"]),
        Route("/ingest", gateway.handle_ingest, methods=["POST"]),

        # REST: Devices
        Route("/api/devices", gateway.api_devices, methods=["GET"]),
        Route("/api/devices/{device_id}", gateway.api_device_detail, methods=["GET"]),
        Route("/api/devices/{device_id}/approve", gateway.api_device_approve, methods=["POST"]),
        Route("/api/devices/{device_id}", gateway.api_device_revoke, methods=["DELETE"]),

        # REST: Events
        Route("/api/events", gateway.api_events, methods=["GET"]),

        # REST: Metrics
        Route("/api/metrics/{device_id}/{metric_name}", gateway.api_metrics, methods=["GET"]),

        # REST: Monitor
        Route("/api/monitor", gateway.api_monitor, methods=["GET"]),
        Route("/api/monitor/{action}", gateway.api_monitor_toggle, methods=["POST"]),

        # REST: Ask
        Route("/api/ask", gateway.api_ask, methods=["POST"]),

        # SSE Streaming
        Route("/api/ask/stream", gateway.api_ask_stream, methods=["POST"]),
        Route("/api/events/stream", gateway.api_events_stream, methods=["GET"]),
    ])

    return app
