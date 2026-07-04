"""Agent API request schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from yequ.agent.limits import (
    AGENT_MAX_STEPS_LIMIT,
    DEFAULT_AGENT_MAX_DEPTH,
    DEFAULT_AGENT_MAX_STEPS,
    DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
)


class CreateSessionRequest(BaseModel):
    actor_id: str = Field(default="agent")
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )


class AgentContextRef(BaseModel):
    type: str = Field(..., min_length=1)
    operation_id: str | None = Field(default=None)
    mode: str = Field(default="observation")


class InvokeAgentRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="fake")
    prompt: str = Field(..., min_length=1)
    user_visible_prompt: str | None = Field(default=None, max_length=8000)
    context_refs: list[AgentContextRef] = Field(default_factory=list, max_length=8)
    target_node_id: str | None = Field(default=None)
    suppress_user_message: bool = Field(default=False)
    call_path: list[str] = Field(default_factory=list)
    step_count: int = Field(default=0, ge=0)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )


class AgentPlanRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    prompt: str = Field(..., min_length=1)
    target_node_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )


class ResumeOperationRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    operation_id: str = Field(..., min_length=1)
    user_message: str | None = Field(default=None)
    prompt: str = Field(
        default=(
            "请根据这个 operation 的最新状态继续。"
            "如果它已经完成，请总结结果；如果失败，请解释原因和下一步。"
        ),
        min_length=1,
    )
    target_node_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )


class ResumeAgentRunRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    agent_run_id: str = Field(..., min_length=1)
    prompt: str = Field(
        default="请根据上一个等待中的 operation 状态继续。",
        min_length=1,
    )
    target_node_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )


class ResumeLastAgentRunRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    target_node_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=DEFAULT_AGENT_MAX_DEPTH, ge=1, le=20)
    max_steps: int = Field(default=DEFAULT_AGENT_MAX_STEPS, ge=1, le=AGENT_MAX_STEPS_LIMIT)
    max_total_duration_sec: int = Field(
        default=DEFAULT_AGENT_MAX_TOTAL_DURATION_SEC,
        ge=1,
        le=3600,
    )
