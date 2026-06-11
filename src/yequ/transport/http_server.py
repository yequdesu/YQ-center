"""HTTP transport layer — Starlette-based server with /hello and /ingest endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
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
from yequ.collector.runner import CollectorRunner
from yequ.storage.ingest import ingest_snapshot, ingest_metric

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Backoff configuration
INITIAL_RETRIES = 10
INITIAL_INTERVAL = 30
MAX_INTERVAL = 600  # 10 minutes


def _compute_retry_after(retry_count: int) -> int:
    """Compute retry_after based on retry count with exponential backoff."""
    if retry_count < INITIAL_RETRIES:
        return INITIAL_INTERVAL
    # Exponential backoff: 30 * 2^(n-10), capped at MAX_INTERVAL
    exponent = retry_count - INITIAL_RETRIES
    interval = INITIAL_INTERVAL * (2 ** exponent)
    return min(interval, MAX_INTERVAL)


class GatewayApp:
    """Starlette application handling YQP HTTP endpoints."""

    def __init__(
        self,
        db_path: str,
        device_store: DeviceStore,
        notify_router: NotifyRouter,
        collector_runner: CollectorRunner,
    ):
        self.db_path = db_path
        self.store = device_store
        self.notify = notify_router
        self.collector = collector_runner

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
        # Check if device already approved
        device = self.store.get_device(msg.device_id)
        if device:
            return JSONResponse(
                RegistrationResponse(
                    status="approved",
                    token=device.token,
                    config={"collector": {"interval_seconds": 60}},
                ).to_dict(),
            )

        # Check existing pending registration
        pending = self.store.get_pending_registration(msg.device_id)
        if pending is None:
            # First time — store as pending
            self.store.add_pending_registration(msg.device_id, msg.device_info)
            retry_count = 0
        else:
            retry_count = self.store.increment_retry(msg.device_id)

        retry_after = _compute_retry_after(retry_count)

        # If retries exhausted (> 1 hour), let device know
        if retry_count >= INITIAL_RETRIES + 10:  # ~1 hour of total waiting
            return JSONResponse(
                {"status": "pending", "retry_after": retry_after, "note": "请联系管理员审批"},
            )

        return JSONResponse(HelloResponse(
            status="pending",
            retry_after=retry_after,
        ).to_dict())

    def _handle_heartbeat_hello(self, msg: HelloHeartbeat) -> JSONResponse:
        device = self.store.get_device_by_token(msg.token)
        if device is None or device.device_id != msg.device_id:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        self.store.touch_hello(msg.device_id)

        # Check for pending commands
        pending_commands = []  # Phase 4 enhancement

        return JSONResponse(Ack(
            message_id="",
            status="ok",
            pending_commands=pending_commands,
        ).to_dict())

    async def handle_ingest(self, request: Request) -> JSONResponse:
        try:
            body = await request.body()
            msg = parse_ingest(body.decode("utf-8"))
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

        # Verify token
        device = self.store.get_device_by_token(msg.token)
        if device is None:
            return JSONResponse({"status": "error", "error": "unauthorized"}, status_code=401)

        # Route by data type
        cap = self.store.get_capability(msg.device_id, msg.capability)
        data_type = cap.data_type if cap else "snapshot"

        if data_type == "snapshot":
            ingest_snapshot(
                self.db_path,
                msg.device_id,
                msg.capability,
                msg.schema_version,
                msg.payload,
                timestamp=msg.timestamp,
            )
        elif data_type == "metric":
            # Each key in payload becomes a metric
            for key, value in msg.payload.items():
                if isinstance(value, (int, float)):
                    ingest_metric(
                        self.db_path,
                        msg.device_id,
                        msg.capability,
                        key,
                        float(value),
                        timestamp=msg.timestamp,
                    )

        return JSONResponse(Ack(message_id=msg.message_id, status="ok").to_dict())


def create_app(
    db_path: str,
    device_store: DeviceStore,
    notify_router: NotifyRouter,
    collector_runner: CollectorRunner,
) -> Starlette:
    """Create and configure the Starlette application."""
    gateway = GatewayApp(
        db_path=db_path,
        device_store=device_store,
        notify_router=notify_router,
        collector_runner=collector_runner,
    )

    app = Starlette(routes=[
        Route("/hello", gateway.handle_hello, methods=["POST"]),
        Route("/ingest", gateway.handle_ingest, methods=["POST"]),
    ])

    return app
