"""HTTP-only YCR client boundary used by Center and Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from yequ.config import Settings, get_settings


class YcrError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, object]:
        return {"error_code": self.code, "message": self.message}


class YcrClient(Protocol):
    async def status(self) -> dict[str, object]: ...

    async def upsert_ref(self, **payload: object) -> dict[str, object]: ...

    async def inspect(self, ref_id: str) -> dict[str, object]: ...

    async def expand(self, ref_id: str, *, path: str = "$", limit: int = 20) -> dict[str, object]:
        ...

    async def tail(self, ref_id: str, *, path: str = "$", lines: int = 40) -> dict[str, object]:
        ...

    async def schema(self, ref_id: str, *, path: str = "$") -> dict[str, object]: ...

    async def search(self, ref_id: str, *, query: str, limit: int = 10) -> dict[str, object]:
        ...

    async def rehydrate(self, ref_id: str) -> dict[str, object]: ...

    async def build_turn(self, **payload: object) -> dict[str, object]: ...

    async def project_tool_observation(self, **payload: object) -> dict[str, object]: ...

    async def prompt_with_context(
        self,
        prompt: str,
        context_blocks: list[dict[str, object]],
    ) -> str: ...

    async def project_context_blocks(
        self,
        blocks: list[dict[str, object]],
    ) -> list[dict[str, object]]: ...

    async def operation_resume_prompt(
        self,
        observation: dict[str, object],
        *,
        user_message: str = "",
    ) -> str: ...

    async def agent_run_resume_prompt(
        self,
        run_projection: dict[str, object],
        *,
        operation_observation: dict[str, object] | None,
    ) -> str: ...

    async def tool_search(
        self,
        *,
        query: str | None = None,
        node_id: str | None = None,
        platform_os: str | None = None,
        limit: int = 10,
        filters: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    async def tool_describe(
        self,
        *,
        capability_ref: str,
        node_id: str | None = None,
        sections: list[str] | None = None,
        projection: str = "invoke_ready",
    ) -> dict[str, object]: ...


@dataclass(slots=True)
class HttpYcrClient:
    base_url: str
    timeout_sec: float = 10.0
    service_token: str = ""

    def _headers(self) -> dict[str, str]:
        if not self.service_token:
            return {}
        return {"Authorization": f"Bearer {self.service_token}"}

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_sec,
                headers=self._headers(),
            ) as client:
                response = await client.request(method, path, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            code, message = _error_from_response(exc.response)
            raise YcrError(code, message) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise YcrError("context_router_unavailable", f"YCR request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise YcrError("context_router_invalid_response", "YCR returned a non-object response")
        if data.get("status") == "failed" and isinstance(data.get("error"), dict):
            error = data["error"]
            raise YcrError(str(error.get("code") or "context_router_error"), str(error))
        return data

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        return await self._request("POST", path, payload)

    async def status(self) -> dict[str, object]:
        return await self._request("GET", "/v1/context/status")

    async def upsert_ref(self, **payload: object) -> dict[str, object]:
        return await self._post("/v1/context/refs", dict(payload))

    async def inspect(self, ref_id: str) -> dict[str, object]:
        return await self._post("/v1/context/inspect", {"ref_id": ref_id})

    async def expand(self, ref_id: str, *, path: str = "$", limit: int = 20) -> dict[str, object]:
        return await self._post(
            "/v1/context/expand",
            {"ref_id": ref_id, "path": path, "limit": limit},
        )

    async def tail(self, ref_id: str, *, path: str = "$", lines: int = 40) -> dict[str, object]:
        return await self._post(
            "/v1/context/tail",
            {"ref_id": ref_id, "path": path, "lines": lines},
        )

    async def schema(self, ref_id: str, *, path: str = "$") -> dict[str, object]:
        return await self._post("/v1/context/schema", {"ref_id": ref_id, "path": path})

    async def search(self, ref_id: str, *, query: str, limit: int = 10) -> dict[str, object]:
        return await self._post(
            "/v1/context/search",
            {"ref_id": ref_id, "query": query, "limit": limit},
        )

    async def rehydrate(self, ref_id: str) -> dict[str, object]:
        return await self._post("/v1/context/rehydrate", {"ref_id": ref_id})

    async def build_turn(self, **payload: object) -> dict[str, object]:
        return await self._post("/v1/context/build-turn", dict(payload))

    async def project_tool_observation(self, **payload: object) -> dict[str, object]:
        return await self._post("/v1/project/tool-observation", dict(payload))

    async def prompt_with_context(
        self,
        prompt: str,
        context_blocks: list[dict[str, object]],
    ) -> str:
        data = await self._post(
            "/v1/project/prompt-with-context",
            {"prompt": prompt, "context_blocks": context_blocks},
        )
        projected = data.get("prompt")
        if not isinstance(projected, str):
            raise YcrError("context_router_invalid_response", "YCR projected prompt is invalid")
        return projected

    async def project_context_blocks(
        self,
        blocks: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        data = await self._post("/v1/project/context-blocks", {"blocks": blocks})
        projected = data.get("blocks")
        if not isinstance(projected, list):
            raise YcrError("context_router_invalid_response", "YCR projected blocks are invalid")
        return [item for item in projected if isinstance(item, dict)]

    async def operation_resume_prompt(
        self,
        observation: dict[str, object],
        *,
        user_message: str = "",
    ) -> str:
        data = await self._post(
            "/v1/project/operation-resume-prompt",
            {"observation": observation, "user_message": user_message},
        )
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            raise YcrError("context_router_invalid_response", "YCR resume prompt is invalid")
        return prompt

    async def agent_run_resume_prompt(
        self,
        run_projection: dict[str, object],
        *,
        operation_observation: dict[str, object] | None,
    ) -> str:
        data = await self._post(
            "/v1/project/agent-run-resume-prompt",
            {
                "run_projection": run_projection,
                "operation_observation": operation_observation,
            },
        )
        prompt = data.get("prompt")
        if not isinstance(prompt, str):
            raise YcrError("context_router_invalid_response", "YCR resume prompt is invalid")
        return prompt

    async def tool_search(
        self,
        *,
        query: str | None = None,
        node_id: str | None = None,
        platform_os: str | None = None,
        limit: int = 10,
        filters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "node_id": node_id,
            "platform_os": platform_os,
            "limit": limit,
        }
        if query is not None:
            payload["query"] = query
        if filters:
            payload.update(filters)
        return await self._post(
            "/v1/tool/search",
            payload,
        )

    async def tool_describe(
        self,
        *,
        capability_ref: str,
        node_id: str | None = None,
        sections: list[str] | None = None,
        projection: str = "invoke_ready",
    ) -> dict[str, object]:
        return await self._post(
            "/v1/tool/describe",
            {
                "capability_ref": capability_ref,
                "node_id": node_id,
                "sections": sections or [],
                "projection": projection,
            },
        )


def get_ycr_client(*, settings: Settings | None = None) -> YcrClient:
    settings = settings or get_settings()
    if settings.ycr_backend != "http":
        raise YcrError("ycr_backend_invalid", "YCR backend must be http")
    return HttpYcrClient(
        base_url=settings.ycr_base_url,
        timeout_sec=settings.ycr_timeout_sec,
        service_token=settings.ycr_service_token,
    )


def ycr_error_payload(exc: YcrError) -> str:
    return json.dumps({"status": "failed", "ycr_error": exc.to_dict()}, ensure_ascii=False)


def _error_from_response(response: httpx.Response) -> tuple[str, str]:
    try:
        data = response.json()
    except ValueError:
        return (
            f"context_router_http_{response.status_code}",
            f"YCR request failed with HTTP {response.status_code}",
        )
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            code = str(
                detail.get("error_code")
                or detail.get("code")
                or f"context_router_http_{response.status_code}"
            )
            message = str(detail.get("message") or detail)
            return code, message
        if isinstance(detail, str) and detail:
            return f"context_router_http_{response.status_code}", detail
        if data.get("status") == "failed" and isinstance(data.get("error"), dict):
            error = data["error"]
            return (
                str(error.get("error_code") or error.get("code") or "context_router_error"),
                str(error.get("message") or error),
            )
    return (
        f"context_router_http_{response.status_code}",
        f"YCR request failed with HTTP {response.status_code}",
    )
