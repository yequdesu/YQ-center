"""Standalone YeQu Context Router service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import NoReturn

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from yequ.config import get_settings
from yequ.db import async_session_factory
from yequ.ycr.budget import projection_profile_from_settings
from yequ.ycr.projection import tool_observation_shell
from yequ.ycr.rag_cache import rag_cache_stats
from yequ.ycr.ref_store import (
    expand_ref as expand_ref_store,
)
from yequ.ycr.ref_store import (
    index_ref_chunks,
    search_context,
    upsert_ref,
)
from yequ.ycr.ref_store import (
    inspect_ref as inspect_ref_store,
)
from yequ.ycr.ref_store import (
    schema_ref as schema_ref_store,
)
from yequ.ycr.ref_store import (
    tail_ref as tail_ref_store,
)

log = logging.getLogger(__name__)
_capability_index_worker_task: asyncio.Task[None] | None = None


class RefRequest(BaseModel):
    ref_id: str | None = None
    path: str = "$"
    limit: int = Field(default=20, ge=1, le=100)
    lines: int = Field(default=40, ge=1, le=200)
    query: str = ""
    session_id: str | None = None


class RefUpsertRequest(BaseModel):
    ref_type: str
    source_type: str
    source_id: str
    path: str = "$"
    value: object
    summary: str
    actor_id: str | None = None
    session_id: str | None = None
    trust_level: str = "node_reported_fact"


class ToolObservationRequest(BaseModel):
    name: str
    call_id: str
    status: str
    result: object
    target_node_id: object | None = None
    actor_id: str | None = None
    session_id: str | None = None
    error: object | None = None
    error_code: object | None = None
    error_details: object | None = None


class BuildTurnRequest(BaseModel):
    session_id: str
    actor_id: str | None = None
    provider: str
    model: str = ""
    messages: list[dict[str, object]] = Field(default_factory=list)
    available_functions: list[dict[str, object]] = Field(default_factory=list)
    capability_context: dict[str, object] = Field(default_factory=dict)
    step: int | None = None


class ToolSearchRequest(BaseModel):
    query: str | None = None
    node_id: str | None = None
    platform_os: str | None = None
    effect: str | None = None
    risk: str | None = None
    runtime_kind: str | None = None
    runtime_labels: list[str] = Field(default_factory=list)
    supports_progress: bool | None = None
    supports_cancel: bool | None = None
    supports_resume: bool | None = None
    preflight_supported: bool | None = None
    artifact_input: bool | None = None
    artifact_output: bool | None = None
    projection: str = "summary"
    capability_type: str = "function"
    include_inactive: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ToolDescribeRequest(BaseModel):
    capability_ref: str
    node_id: str | None = None
    sections: list[str] = Field(default_factory=list)
    projection: str = "invoke_ready"


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    global _capability_index_worker_task
    if (
        not get_settings().test_mode
        and (_capability_index_worker_task is None or _capability_index_worker_task.done())
    ):
        _capability_index_worker_task = asyncio.create_task(_capability_index_worker())
    try:
        yield
    finally:
        if _capability_index_worker_task is not None:
            _capability_index_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _capability_index_worker_task


app = FastAPI(title="YeQu Context Router", version="2.0.0", lifespan=_lifespan)


async def _capability_index_worker() -> None:
    from yequ.ycr.capability_index_jobs import run_capability_index_jobs_once

    while True:
        try:
            async with async_session_factory() as db:
                await run_capability_index_jobs_once(db, limit=10)
                await db.commit()
        except Exception:
            log.exception("YCR capability index worker failed")
        await asyncio.sleep(5)


async def _index_ref_background(ref_id: str) -> None:
    try:
        async with async_session_factory() as db:
            await index_ref_chunks(db, ref_id)
            await db.commit()
    except Exception:
        log.exception("YCR context ref indexing failed: ref_id=%s", ref_id)


def _profile():
    return projection_profile_from_settings(get_settings())


def _require_ycr_auth(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if settings.test_mode:
        return
    if not settings.ycr_service_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YCR service token is not configured",
        )
    expected = f"Bearer {settings.ycr_service_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid YCR service token",
        )


def _raise_ref_error(exc: ValueError) -> NoReturn:
    message = str(exc)
    lowered = message.lower()
    if "raw_result_too_large" in lowered:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "raw_result_too_large", "message": message},
        ) from exc
    if "not found" in lowered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "context_ref_not_found", "message": message},
        ) from exc
    if "context_search_unavailable" in lowered:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "context_search_unavailable", "message": message},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error_code": "context_ref_invalid_request", "message": message},
    ) from exc


def _raise_ycr_value_error(exc: ValueError) -> NoReturn:
    message = str(exc)
    lowered = message.lower()
    error_map = {
        "unprojected_tool_observation": (
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unprojected_tool_observation",
        ),
        "context_ref_not_found": (status.HTTP_404_NOT_FOUND, "context_ref_not_found"),
        "capability_index_not_ready": (
            status.HTTP_409_CONFLICT,
            "capability_index_not_ready",
        ),
        "capability_rag_unavailable": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "capability_rag_unavailable",
        ),
        "capability_rerank_unavailable": (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "capability_rerank_unavailable",
        ),
        "invalid_capability_search_request": (
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_capability_search_request",
        ),
    }
    for marker, (http_status, code) in error_map.items():
        if marker in lowered:
            raise HTTPException(
                status_code=http_status,
                detail={"error_code": code, "message": message},
            ) from exc
    if "not found" in lowered or "no active capability source matches" in lowered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "capability_not_found", "message": message},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error_code": "context_router_invalid_request", "message": message},
    ) from exc


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"status": "ok", "component": "ycr"}


@app.get("/v1/context/status", dependencies=[Depends(_require_ycr_auth)])
async def context_status() -> dict[str, object]:
    from yequ.ycr.capability_index_jobs import capability_index_status

    settings = get_settings()
    database_url = settings.database_url.lower()
    vector_backend = (
        "postgresql_pgvector" if database_url.startswith("postgresql") else "local_vector_scan"
    )
    async with async_session_factory() as db:
        index_status = await capability_index_status(db)
    return {
        "status": "ready",
        "mode": "standalone",
        "vector_backend": vector_backend,
        "embedding_provider": settings.ycr_embedding_provider,
        "embedding_model": settings.ycr_embedding_model,
        "rerank_model": settings.ycr_rerank_model,
        "capability_discovery": {
            "strategies": ["registry_filter_v2", "tool_rag_bge_m3_rrf_rerank_v2"],
            "tool_rag": (
                "enabled"
                if settings.ycr_embedding_provider == "openai_compatible"
                and bool(settings.ycr_embedding_base_url)
                else "unavailable"
            ),
            "rag_cache": rag_cache_stats(),
            "index": index_status,
        },
        "projection": {
            "provider": _profile().provider,
            "model": _profile().model,
            "default_inline_bytes": _profile().default.inline_bytes,
            "default_preview_chars": _profile().default.preview_chars,
        },
        "capabilities": [
            "build_turn",
            "refs",
            "tool_observations",
            "inspect",
            "expand",
            "tail",
            "schema",
            "search",
            "tool_search",
            "tool_describe",
        ],
    }


@app.post("/v1/context/refs", dependencies=[Depends(_require_ycr_auth)])
async def upsert_context_ref(body: RefUpsertRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            ref = await upsert_ref(
                db,
                ref_type=body.ref_type,
                source_type=body.source_type,
                source_id=body.source_id,
                path=body.path,
                value=body.value,
                summary=body.summary,
                actor_id=body.actor_id,
                session_id=body.session_id,
                trust_level=body.trust_level,
                projection_policy="context_ref_v2",
            )
            await db.commit()
            asyncio.create_task(_index_ref_background(str(ref["ref_id"])))
            return ref
        except ValueError as exc:
            await db.rollback()
            _raise_ref_error(exc)


@app.post("/v1/tool-observations", dependencies=[Depends(_require_ycr_auth)])
async def store_tool_observation(body: ToolObservationRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            raw_ref = await upsert_ref(
                db,
                ref_type="tool_result",
                source_type="tool_call",
                source_id=body.call_id,
                path="$",
                value=body.result,
                summary=f"{body.name} {body.status}",
                actor_id=body.actor_id,
                session_id=body.session_id,
                trust_level="node_reported_fact",
                projection_policy="tool_observation_raw_ref_v1",
            )
            shell = tool_observation_shell(
                name=body.name,
                call_id=body.call_id,
                status=body.status,
                raw_ref=raw_ref,
                target_node_id=body.target_node_id,
                error=body.error,
                error_code=body.error_code,
                error_details=body.error_details,
            )
            await db.commit()
            asyncio.create_task(_index_ref_background(str(raw_ref["ref_id"])))
            return {
                "raw_ref": raw_ref,
                "shell": shell,
                "ycr": {
                    "stored": True,
                    "projection_policy": "tool_observation_raw_ref_v1",
                },
            }
        except ValueError as exc:
            await db.rollback()
            _raise_ref_error(exc)


@app.post("/v1/context/build-turn", dependencies=[Depends(_require_ycr_auth)])
async def build_turn(body: BuildTurnRequest) -> dict[str, object]:
    from yequ.ycr.context_packet import build_agent_context_packet

    async with async_session_factory() as db:
        try:
            return await build_agent_context_packet(
                db,
                session_id=body.session_id,
                actor_id=body.actor_id,
                provider=body.provider,
                model=body.model,
                messages=body.messages,
                available_functions=body.available_functions,
                capability_context=body.capability_context,
                profile=_profile(),
                step=body.step,
            )
        except ValueError as exc:
            _raise_ycr_value_error(exc)


@app.post("/v1/context/inspect", dependencies=[Depends(_require_ycr_auth)])
async def inspect_ref(body: RefRequest) -> dict[str, object]:
    if not body.ref_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "context_ref_not_found", "message": "ref_id is required"},
        )
    async with async_session_factory() as db:
        try:
            return await inspect_ref_store(db, body.ref_id)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/expand", dependencies=[Depends(_require_ycr_auth)])
async def expand_ref(body: RefRequest) -> dict[str, object]:
    if not body.ref_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "context_ref_not_found", "message": "ref_id is required"},
        )
    async with async_session_factory() as db:
        try:
            return await expand_ref_store(db, body.ref_id, path=body.path, limit=body.limit)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/tail", dependencies=[Depends(_require_ycr_auth)])
async def tail_ref(body: RefRequest) -> dict[str, object]:
    if not body.ref_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "context_ref_not_found", "message": "ref_id is required"},
        )
    async with async_session_factory() as db:
        try:
            return await tail_ref_store(db, body.ref_id, path=body.path, lines=body.lines)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/schema", dependencies=[Depends(_require_ycr_auth)])
async def schema_ref(body: RefRequest) -> dict[str, object]:
    if not body.ref_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "context_ref_not_found", "message": "ref_id is required"},
        )
    async with async_session_factory() as db:
        try:
            return await schema_ref_store(db, body.ref_id, path=body.path)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/search", dependencies=[Depends(_require_ycr_auth)])
async def search_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            result = await search_context(
                db,
                ref_id=body.ref_id,
                session_id=body.session_id,
                query=body.query,
                limit=body.limit,
            )
            await db.commit()
            return result
        except ValueError as exc:
            await db.rollback()
            _raise_ref_error(exc)


@app.post("/v1/context/index", dependencies=[Depends(_require_ycr_auth)])
async def index_ref(body: RefRequest) -> dict[str, object]:
    if not body.ref_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "context_ref_not_found", "message": "ref_id is required"},
        )
    async with async_session_factory() as db:
        try:
            output = await index_ref_chunks(db, body.ref_id)
            await db.commit()
            return output
        except ValueError as exc:
            await db.rollback()
            _raise_ref_error(exc)


@app.post("/v1/tool/search", dependencies=[Depends(_require_ycr_auth)])
async def tool_search(body: ToolSearchRequest) -> dict[str, object]:
    from yequ.ycr.capability_gateway import search_capability_registry

    async with async_session_factory() as db:
        try:
            result = await search_capability_registry(
                db,
                query=body.query,
                node_id=body.node_id,
                platform_os=body.platform_os,
                filters=body.model_dump(exclude_none=True),
                limit=body.limit,
            )
            await db.commit()
            return result
        except ValueError as exc:
            await db.rollback()
            _raise_ycr_value_error(exc)


@app.post("/v1/tool/index/run", dependencies=[Depends(_require_ycr_auth)])
async def run_tool_index_jobs() -> dict[str, object]:
    from yequ.ycr.capability_index_jobs import run_capability_index_jobs_once

    async with async_session_factory() as db:
        result = await run_capability_index_jobs_once(db, limit=50)
        await db.commit()
        return {"status": "ok", **result}


@app.post("/v1/tool/describe", dependencies=[Depends(_require_ycr_auth)])
async def tool_describe(body: ToolDescribeRequest) -> dict[str, object]:
    from yequ.ycr.capability_gateway import describe_capability_registry

    async with async_session_factory() as db:
        try:
            return await describe_capability_registry(
                db,
                capability_ref=body.capability_ref,
                node_id=body.node_id,
                sections=body.sections,
                projection=body.projection,
            )
        except ValueError as exc:
            _raise_ycr_value_error(exc)
