import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentResult
from yequ.api.routes.agent import register_provider
from yequ.models.session import Session
from yequ.runtime.agent_run_service import (
    append_agent_run_step,
    create_agent_run,
    update_agent_run_status,
)


@pytest.mark.asyncio
async def test_resume_last_run_stream_injects_agent_run_checkpoint(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    session_id = "sess_resume_last_run"
    db_session.add(
        Session(
            session_id=session_id,
            actor_type="agent",
            actor_id="test-agent",
            status="active",
            execution_mode="auto",
            label="resume-last",
        )
    )
    run = await create_agent_run(
        db_session,
        session_id=session_id,
        provider_name="fake-resume-last-run",
        execution_mode="auto",
        target_node_id=None,
        user_message="transfer a large file",
        trace_id="tr_resume_last",
        metadata={"source": "test"},
    )
    await append_agent_run_step(
        db_session,
        run,
        step_index=1,
        step_type="provider",
        status="failed",
        output_data={"assistant_text": "starting transfer", "tool_calls": []},
        error_code="llm_error",
        error_message="Request timed out.",
    )
    await update_agent_run_status(
        db_session,
        run,
        status="failed",
        error_code="llm_error",
        error_message="Request timed out.",
        metadata={"waiting": {"status": "failed"}},
    )
    await db_session.commit()

    provider = FakeAgentProvider(provider_name="fake-resume-last-run")
    provider.set_default_result(
        AgentResult(
            success=True,
            output={"message": "checkpoint summarized"},
            function_calls=[],
        )
    )
    register_provider(provider)

    response = await client.post(
        "/agent/resume-last-run/stream",
        json={
            "session_id": session_id,
            "provider_name": "fake-resume-last-run",
            "execution_mode": "auto",
        },
    )

    assert response.status_code == 200
    assert "checkpoint summarized" in response.text
    assert provider.last_messages is not None
    user_messages = [
        message.content
        for message in provider.last_messages
        if message.role == "user" and message.content
    ]
    assert user_messages
    assert run.run_id in user_messages[-1]
    assert "AgentRun checkpoint" in user_messages[-1]
    assert "Request timed out." in user_messages[-1]


@pytest.mark.asyncio
async def test_agent_run_terminal_status_is_immutable(db_session: AsyncSession) -> None:
    run = await create_agent_run(
        db_session,
        session_id="sess_terminal_immutable",
        provider_name="fake-terminal",
        execution_mode="auto",
        target_node_id=None,
        user_message="finish",
        trace_id="tr_terminal",
        metadata={"source": "test"},
    )
    await update_agent_run_status(
        db_session,
        run,
        status="succeeded",
        final_message="done",
    )

    with pytest.raises(ValueError, match="is terminal"):
        await update_agent_run_status(
            db_session,
            run,
            status="waiting_operation",
            metadata={"waiting": {"operation_id": "op_late"}},
        )

    assert run.status == "succeeded"
    assert run.final_message == "done"

