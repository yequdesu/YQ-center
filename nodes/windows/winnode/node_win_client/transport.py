from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings
from .logging_config import get_logger
from .models import Envelope


class CenterTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("transport")
        self.client = httpx.AsyncClient(
            base_url=settings.center_base_url.rstrip("/"),
            timeout=settings.request_timeout_sec,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {settings.node_token}",
                "Content-Type": "application/json",
                "User-Agent": "YeQu-Node-winClient/0.1",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def send(self, message: Envelope, route_key: str | None = None) -> dict[str, Any]:
        key = route_key or message.message_type
        route = self.settings.routes.get(key, "/yqp")
        started = time.perf_counter()
        self.logger.info(
            "center.request start message_type=%s message_id=%s route=%s base_url=%s",
            message.message_type,
            message.message_id,
            route,
            self.settings.center_base_url.rstrip("/"),
        )
        try:
            response = await self.client.post(
                route,
                json=message.model_dump(mode="json", exclude_none=True),
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.logger.info(
                "center.request response message_type=%s message_id=%s route=%s "
                "status=%s elapsed_ms=%s bytes=%s",
                message.message_type,
                message.message_id,
                route,
                response.status_code,
                elapsed_ms,
                len(response.content or b""),
            )
            response.raise_for_status()
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.logger.warning(
                "center.request failed message_type=%s message_id=%s route=%s "
                "elapsed_ms=%s error_type=%s error=%s",
                message.message_type,
                message.message_id,
                route,
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            raise
        if not response.content:
            return {"message_type": "empty", "payload": {}}
        data = response.json()
        if isinstance(data, dict):
            self.logger.info(
                "center.request decoded message_type=%s message_id=%s response_type=%s",
                message.message_type,
                message.message_id,
                data.get("message_type"),
            )
            return data
        return {"message_type": "raw", "payload": data}

    async def post_route(self, route_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        route = self.settings.routes[route_key]
        headers = None
        if self.settings.api_token:
            headers = {"Authorization": f"Bearer {self.settings.api_token}"}
        started = time.perf_counter()
        self.logger.info("center.api_request start route_key=%s route=%s", route_key, route)
        try:
            response = await self.client.post(route, json=payload, headers=headers)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.logger.info(
                "center.api_request response route_key=%s route=%s status=%s "
                "elapsed_ms=%s bytes=%s",
                route_key,
                route,
                response.status_code,
                elapsed_ms,
                len(response.content or b""),
            )
            response.raise_for_status()
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self.logger.warning(
                "center.api_request failed route_key=%s route=%s elapsed_ms=%s "
                "error_type=%s error=%s",
                route_key,
                route,
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            raise
        if not response.content:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            return {"data": data}
        return data
