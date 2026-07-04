"""Standalone YeQu Context Router service."""

from __future__ import annotations

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


class ToolRecommendRequest(ToolSearchRequest):
    query: str


class ToolDescribeRequest(BaseModel):
    capability_ref: str
    node_id: str | None = None
    sections: list[str] = Field(default_factory=list)


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


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {"status": "ok", "component": "ycr"}


@app.get("/v1/context/status", dependencies=[Depends(_require_ycr_auth)])
async def context_status() -> dict[str, object]:
    budget = _budget()
    return {
        "status": "ready",
        "mode": "standalone",
        "vector_backend": "pgvector",
        "embedding_provider": get_settings().ycr_embedding_provider,
        "embedding_model": get_settings().ycr_embedding_model,
        "budget": {
            "provider": budget.provider,
            "model": budget.model,
            "max_input_tokens": budget.max_input_tokens,
            "reserved_response_tokens": budget.reserved_response_tokens,
        },
        "capabilities": [
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


@app.post("/v1/context/inspect", dependencies=[Depends(_require_ycr_auth)])
async def inspect_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        return await inspect_ref_store(db, body.ref_id)


@app.post("/v1/context/expand", dependencies=[Depends(_require_ycr_auth)])
async def expand_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        return await expand_ref_store(db, body.ref_id, path=body.path, limit=body.limit)


@app.post("/v1/context/tail", dependencies=[Depends(_require_ycr_auth)])
async def tail_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        return await tail_ref_store(db, body.ref_id, path=body.path, lines=body.lines)


@app.post("/v1/context/schema", dependencies=[Depends(_require_ycr_auth)])
async def schema_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        return await schema_ref_store(db, body.ref_id, path=body.path)


@app.post("/v1/context/search", dependencies=[Depends(_require_ycr_auth)])
async def search_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        result = await search_ref_store(db, body.ref_id, query=body.query, limit=body.limit)
        await db.commit()
        return result


@app.post("/v1/context/rehydrate", dependencies=[Depends(_require_ycr_auth)])
async def rehydrate_ref(body: RefRequest) -> dict[str, object]:
    async with async_session_factory() as db:
        ref = await rehydrate_ref_store(db, body.ref_id)
        await db.commit()
        return ref


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
    from yequ.ycr.tool_rag import retrieve_tool_context

    async with async_session_factory() as db:
        return await retrieve_tool_context(
            db,
            query=body.query,
            node_id=body.node_id,
            platform_os=body.platform_os,
            filters=body.model_dump(exclude_none=True),
            limit=body.limit,
        )


@app.post("/v1/tool/recommend", dependencies=[Depends(_require_ycr_auth)])
async def tool_recommend(body: ToolRecommendRequest) -> dict[str, object]:
    from yequ.ycr.tool_rag import recommend_tool_context

    async with async_session_factory() as db:
        return await recommend_tool_context(
            db,
            query=body.query,
            node_id=body.node_id,
            platform_os=body.platform_os,
            filters=body.model_dump(exclude_none=True),
            limit=body.limit,
        )


@app.post("/v1/tool/describe", dependencies=[Depends(_require_ycr_auth)])
async def tool_describe(body: ToolDescribeRequest) -> dict[str, object]:
    from yequ.ycr.tool_rag import describe_tool_context

    async with async_session_factory() as db:
        return await describe_tool_context(
            db,
            capability_ref=body.capability_ref,
            node_id=body.node_id,
            sections=body.sections,
        )
