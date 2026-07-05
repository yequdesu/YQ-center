"""Standalone YeQu Context Router service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from yequ.config import get_settings
from yequ.db import async_session_factory
from yequ.ycr import projection
from yequ.ycr.budget import budget_profile_from_settings
from yequ.ycr.ref_store import (
    expand_ref as expand_ref_store,
)
from yequ.ycr.ref_store import (
    inspect_ref as inspect_ref_store,
)
from yequ.ycr.ref_store import (
    rehydrate_ref as rehydrate_ref_store,
)
from yequ.ycr.ref_store import (
    schema_ref as schema_ref_store,
)
from yequ.ycr.ref_store import (
    search_ref as search_ref_store,
)
from yequ.ycr.ref_store import (
    tail_ref as tail_ref_store,
)
from yequ.ycr.ref_store import (
    upsert_ref,
)


class RefRequest(BaseModel):
    ref_id: str
    path: str = "$"
    limit: int = Field(default=20, ge=1, le=100)
    lines: int = Field(default=40, ge=1, le=200)
    query: str = ""


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
    ttl_sec: int = Field(default=86400, ge=60, le=30 * 86400)


class ToolObservationRequest(BaseModel):
    name: str
    call_id: str
    status: str
    result: object
    target_node_id: object | None = None


class ToolMessageRequest(BaseModel):
    content: str


class ContextBlocksRequest(BaseModel):
    blocks: list[dict[str, object]] = Field(default_factory=list)


class PromptWithContextRequest(BaseModel):
    prompt: str
    context_blocks: list[dict[str, object]] = Field(default_factory=list)


class OperationResumePromptRequest(BaseModel):
    observation: dict[str, object]
    user_message: str = ""


class AgentRunResumePromptRequest(BaseModel):
    run_projection: dict[str, object]
    operation_observation: dict[str, object] | None = None


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


app = FastAPI(title="YeQu Context Router", version="1.0.0")


def _budget():
    return budget_profile_from_settings(get_settings())


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
    if "not found" in lowered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "context_ref_not_found",
                "message": message,
            },
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error_code": "context_ref_invalid_request",
            "message": message,
        },
    ) from exc


def _raise_ycr_value_error(exc: ValueError) -> NoReturn:
    message = str(exc)
    lowered = message.lower()
    if "context_budget_exceeded" in lowered:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error_code": "context_budget_exceeded", "message": message},
        ) from exc
    if "unprojected_tool_observation" in lowered:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "unprojected_tool_observation", "message": message},
        ) from exc
    if "not found" in lowered or "no active capability source matches" in lowered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "capability_not_found", "message": message},
        ) from exc
    if "capability_rag_unavailable" in lowered:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "capability_rag_unavailable", "message": message},
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
    budget = _budget()
    settings = get_settings()
    database_url = settings.database_url.lower()
    vector_backend = (
        "postgresql_pgvector" if database_url.startswith("postgresql") else "local_vector_scan"
    )
    return {
        "status": "ready",
        "mode": "standalone",
        "vector_backend": vector_backend,
        "embedding_provider": settings.ycr_embedding_provider,
        "embedding_model": settings.ycr_embedding_model,
        "capability_discovery": {
            "strategies": ["registry_filter_v1", "tool_rag_bge_m3_rrf_v1"],
            "tool_rag": (
                "enabled"
                if settings.ycr_embedding_provider == "openai_compatible"
                and bool(settings.ycr_embedding_base_url)
                else "unavailable"
            ),
            "tool_rag_model": settings.ycr_embedding_model,
        },
        "budget": {
            "provider": budget.provider,
            "model": budget.model,
            "max_input_tokens": budget.max_input_tokens,
            "reserved_response_tokens": budget.reserved_response_tokens,
        },
        "capabilities": [
            "build_turn",
            "refs",
            "inspect",
            "expand",
            "tail",
            "schema",
            "search",
            "rehydrate",
            "project",
        ],
    }


@app.post("/v1/context/refs", dependencies=[Depends(_require_ycr_auth)])
async def upsert_context_ref(body: RefUpsertRequest) -> dict[str, object]:
    async with async_session_factory() as db:
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
            ttl_sec=body.ttl_sec,
        )
        await db.commit()
        return ref


@app.post("/v1/context/build-turn", dependencies=[Depends(_require_ycr_auth)])
async def build_turn(body: BuildTurnRequest) -> dict[str, object]:
    from yequ.ycr.context_packet import build_agent_context_packet

    try:
        return build_agent_context_packet(
            session_id=body.session_id,
            actor_id=body.actor_id,
            provider=body.provider,
            model=body.model,
            messages=body.messages,
            available_functions=body.available_functions,
            capability_context=body.capability_context,
            budget=_budget(),
            step=body.step,
        )
    except ValueError as exc:
        _raise_ycr_value_error(exc)


@app.post("/v1/context/inspect", dependencies=[Depends(_require_ycr_auth)])
async def inspect_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            return await inspect_ref_store(db, body.ref_id)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/expand", dependencies=[Depends(_require_ycr_auth)])
async def expand_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            return await expand_ref_store(db, body.ref_id, path=body.path, limit=body.limit)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/tail", dependencies=[Depends(_require_ycr_auth)])
async def tail_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            return await tail_ref_store(db, body.ref_id, path=body.path, lines=body.lines)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/schema", dependencies=[Depends(_require_ycr_auth)])
async def schema_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            return await schema_ref_store(db, body.ref_id, path=body.path)
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/search", dependencies=[Depends(_require_ycr_auth)])
async def search_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            result = await search_ref_store(db, body.ref_id, query=body.query, limit=body.limit)
            await db.commit()
            return result
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/context/rehydrate", dependencies=[Depends(_require_ycr_auth)])
async def rehydrate_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        try:
            ref = await rehydrate_ref_store(db, body.ref_id)
            await db.commit()
            return ref
        except ValueError as exc:
            _raise_ref_error(exc)


@app.post("/v1/project/tool-observation", dependencies=[Depends(_require_ycr_auth)])
async def project_tool_observation(body: ToolObservationRequest) -> dict[str, object]:
    return projection.project_tool_observation(
        name=body.name,
        call_id=body.call_id,
        status=body.status,
        result=body.result,
        target_node_id=body.target_node_id,
        budget=_budget(),
    )


@app.post("/v1/project/tool-message", dependencies=[Depends(_require_ycr_auth)])
async def project_tool_message(body: ToolMessageRequest) -> dict[str, object]:
    return {"content": projection.ensure_projected_tool_message(body.content, budget=_budget())}


@app.post("/v1/project/context-blocks", dependencies=[Depends(_require_ycr_auth)])
async def project_context_blocks(body: ContextBlocksRequest) -> dict[str, object]:
    return {"blocks": projection.project_context_blocks(body.blocks, budget=_budget())}


@app.post("/v1/project/prompt-with-context", dependencies=[Depends(_require_ycr_auth)])
async def prompt_with_context(body: PromptWithContextRequest) -> dict[str, object]:
    return {
        "prompt": projection.prompt_with_projected_context(
            body.prompt,
            body.context_blocks,
            budget=_budget(),
        )
    }


@app.post("/v1/project/operation-resume-prompt", dependencies=[Depends(_require_ycr_auth)])
async def operation_resume_prompt(body: OperationResumePromptRequest) -> dict[str, object]:
    return {
        "prompt": projection.operation_resume_prompt(
            body.observation,
            user_message=body.user_message,
            budget=_budget(),
        )
    }


@app.post("/v1/project/agent-run-resume-prompt", dependencies=[Depends(_require_ycr_auth)])
async def agent_run_resume_prompt(body: AgentRunResumePromptRequest) -> dict[str, object]:
    return {
        "prompt": projection.agent_run_resume_prompt(
            body.run_projection,
            operation_observation=body.operation_observation,
            budget=_budget(),
        )
    }


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
            _raise_ycr_value_error(exc)


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
