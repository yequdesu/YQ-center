"""Stage 5: Integration closed-loop tests.

Full end-to-end scenarios using ASGITransport -- no external services.
Fake Node + FakeAgentProvider simulate real clients through HTTP API.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentResult
from yequ.api.app import create_app
from yequ.api.routes.agent import register_provider
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.protocol import InvocationStatus, JobStatus


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def center():
    """Full Center app with fresh tables per test.

    Uses the test database from conftest.py fixtures (db_engine).
    This fixture depends on the autouse override_settings fixture
    which configures the test DB. It creates its own client.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def fake_node(center, db_session):
    """Provision a fake node, do hello + register, return (node_id, token, auth)."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"

    # Provision
    r = await center.post("/admin/nodes", json={
        "node_id": node_id, "node_name": "Fake Node", "token": token,
    })
    assert r.status_code == 201

    auth = {"Authorization": f"Bearer {token}"}

    # Hello
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_hello_{_uid()}",
        "message_type": "node.hello",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {"daemon_version": "0.1.0", "platform": {"os": "linux", "arch": "x86_64"}},
    }, headers=auth)
    assert r.status_code == 200, f"hello failed: {r.text}"
    assert r.json()["message_type"] == "node.accepted"

    # Register capabilities
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_reg_{_uid()}",
        "message_type": "node.register_capabilities",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {"plugins": [{
            "plugin_id": "system.metrics",
            "plugin_version": "1.0.0",
            "functions": [{
                "name": "system.metrics.snapshot",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "risk": "safe", "effect": "read", "timeout_sec": 5,
                "idempotency": "idempotent",
            }],
            "signals": [{
                "name": "cpu.usage", "scope": "node", "ttl_sec": 15,
                "value_schema": {"type": "number", "minimum": 0, "maximum": 100},
            }],
        }]},
    }, headers=auth)
    assert r.status_code == 200

    return node_id, token, auth


def _ts() -> str:
    return datetime.now(UTC).isoformat()


# ── Scenario 1: Success ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_success_flow(center, fake_node):
    """Fake Node: hello -> register -> invocation -> poll -> accept -> succeed."""
    node_id, token, auth = fake_node

    # Create Invocation (spawns a queued Job)
    r = await center.post("/admin/invocations", json={
        "function_name": "system.metrics.snapshot",
        "target_node_id": node_id,
        "input_payload": {},
        "timeout_sec": 30,
        "lease_sec": 10,
    })
    assert r.status_code == 201
    inv_data = r.json()
    job_id = inv_data["job_id"]
    assert inv_data["job_status"] == "queued"

    # Heartbeat
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_hb_{_uid()}",
        "message_type": "node.heartbeat",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": _ts(),
        "payload": {"daemon_uptime_sec": 60, "running_jobs": 0, "status": "online"},
    }, headers=auth)
    assert r.status_code == 200

    # Poll -> get the job
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_poll_{_uid()}",
        "message_type": "job.poll",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": _ts(),
        "payload": {"capacity": 2},
    }, headers=auth)
    assert r.status_code == 200
    jobs = r.json()["payload"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id

    # Accept
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_acc_{_uid()}",
        "message_type": "job.accepted",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": _ts(),
        "payload": {"job_id": job_id},
    }, headers=auth)
    assert r.status_code == 200

    # Finish (succeeded)
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1",
        "message_id": f"msg_fin_{_uid()}",
        "message_type": "job.finished",
        "trace_id": f"tr_{_uid()}",
        "node_id": node_id,
        "timestamp": _ts(),
        "payload": {"job_id": job_id, "status": "succeeded", "output": {"cpu": 42.0}},
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["payload"]["status"] == "succeeded"

    # Verify Job terminal in DB
    from yequ.db import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.SUCCEEDED
        assert job.finished_at is not None
        assert job.output == {"cpu": 42.0}

    # Verify Timeline events were written
    async with async_session_factory() as db:
        result = await db.execute(
            select(TimelineEvent).where(TimelineEvent.job_id == job_id)
        )
        events = result.scalars().all()
        event_types = {e.event_type for e in events}
        assert "job.queued" in event_types
        assert "job.succeeded" in event_types

    print(f"SUCCESS: job={job_id} completed with output={job.output}")


# ── Scenario 2: Failure ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_failure_flow(center, fake_node):
    """Fake Node reports job failure -> Job terminal failed, Invocation failed."""
    node_id, token, auth = fake_node

    r = await center.post("/admin/invocations", json={
        "function_name": "system.metrics.snapshot",
        "target_node_id": node_id,
        "timeout_sec": 30,
    })
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    inv_id = r.json()["invocation_id"]

    # Poll + Accept
    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_poll_fail_{_uid()}",
        "message_type": "job.poll", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"capacity": 1},
    }, headers=auth)

    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_acc_fail_{_uid()}",
        "message_type": "job.accepted", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"job_id": job_id},
    }, headers=auth)

    # Finish (failed)
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_fin_fail_{_uid()}",
        "message_type": "job.finished", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"job_id": job_id, "status": "failed",
                     "error_code": "execution_error", "error_message": "command failed"},
    }, headers=auth)
    assert r.status_code == 200

    # Verify Job in DB
    from yequ.db import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.FAILED
        assert job.error_code == "execution_error"

    # Invocation aggregation via aggregate_invocation_status
    from yequ.services.invocation_service import aggregate_invocation_status
    async with async_session_factory() as db:
        status = await aggregate_invocation_status(db, inv_id)
        assert status == InvocationStatus.FAILED


# ── Scenario 3: Timeout ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_timeout_flow(center, fake_node):
    """Job with short lease -> poll -> expire -> scanner marks timeout."""
    node_id, token, auth = fake_node

    r = await center.post("/admin/invocations", json={
        "function_name": "system.metrics.snapshot",
        "target_node_id": node_id,
        "timeout_sec": 30,
        "lease_sec": 1,  # 1-second lease
    })
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # Poll (claims with 1s lease)
    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_pto_{_uid()}",
        "message_type": "job.poll", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"capacity": 1},
    }, headers=auth)

    # Wait for lease to expire
    await asyncio.sleep(1.5)

    # Trigger scanner manually
    from yequ.services.timeout_scanner import get_scanner
    scanner = get_scanner()
    await scanner._scan()

    # Verify timeout
    from yequ.db import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.TIMEOUT, f"Expected timeout, got {job.status}"
        assert job.finished_at is not None


# ── Scenario 4: Cancel ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_cancel_flow(center, fake_node):
    """Create job -> poll -> accept -> cancel -> finish cancelled."""
    node_id, token, auth = fake_node

    r = await center.post("/admin/invocations", json={
        "function_name": "system.metrics.snapshot",
        "target_node_id": node_id,
        "timeout_sec": 30,
    })
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # Poll + Accept
    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_cpol_{_uid()}",
        "message_type": "job.poll", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"capacity": 1},
    }, headers=auth)
    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_cacc_{_uid()}",
        "message_type": "job.accepted", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"job_id": job_id},
    }, headers=auth)

    # Cancel
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_ccel_{_uid()}",
        "message_type": "job.cancel", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"job_id": job_id, "reason": "user_requested"},
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["payload"]["status"] == "cancelling"

    # Finish (cancelled)
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_cfin_{_uid()}",
        "message_type": "job.finished", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"job_id": job_id, "status": "cancelled"},
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["payload"]["status"] == "cancelled"

    # Verify
    from yequ.db import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.CANCELLED


# ── Scenario 5: Reconcile ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_flow(center, fake_node):
    """Daemon reconnects with known jobs -> Center returns reconciliation actions."""
    node_id, token, auth = fake_node

    from yequ.db import async_session_factory
    from yequ.models.job import Job

    # Create a running job known to Center
    async with async_session_factory() as db:
        job = Job(
            job_id=f"job_rec_run_{_uid()}",
            invocation_id=f"inv_rec_{_uid()}",
            node_id=node_id,
            function_name="system.metrics.snapshot",
            status=JobStatus.RUNNING,
            timeout_sec=30,
            lease_sec=10,
        )
        db.add(job)
        await db.commit()
        known_job_id = job.job_id

    # Reconcile: Daemon says running -> should get continue
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_rec1_{_uid()}",
        "message_type": "node.reconcile_jobs", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"known_jobs": [
            {"job_id": known_job_id, "local_status": "running"},
        ]},
    }, headers=auth)
    assert r.status_code == 200
    actions = r.json()["payload"]["actions"]
    assert len(actions) == 1
    assert actions[0]["action"] == "continue"

    # Reconcile: unknown job -> forget
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_rec2_{_uid()}",
        "message_type": "node.reconcile_jobs", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"known_jobs": [
            {"job_id": f"job_ghost_{_uid()}", "local_status": "running"},
        ]},
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["payload"]["actions"][0]["action"] == "forget"


# ── Scenario 6: Agent invoke ───────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_invoke_with_function_calls(center, fake_node):
    """Agent session -> invoke with FakeAgentProvider returning function_calls."""
    node_id, token, auth = fake_node

    # Configure FakeAgentProvider
    from yequ.agent.provider import AgentFunction
    provider = FakeAgentProvider()
    provider.add_response("metrics", AgentResult(
        success=True,
        output={"action": "get_metrics"},
        function_calls=[{"name": "system.metrics.snapshot", "input": {}}],
    ))
    provider.add_function(AgentFunction(
        name="system.metrics.snapshot", risk="safe", effect="read",
    ))
    register_provider(provider)

    # Create session
    r = await center.post("/agent/sessions", json={
        "actor_id": "test-agent",
        "execution_mode": "auto",
    })
    assert r.status_code == 201
    sid = r.json()["session_id"]

    # Invoke
    r = await center.post("/agent/invoke", json={
        "session_id": sid,
        "provider_name": "fake",
        "prompt": "get metrics please",
        "execution_mode": "auto",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert len(data["function_calls"]) == 1
    assert data["function_calls"][0]["name"] == "system.metrics.snapshot"

    # Verify Timeline events for agent step
    from yequ.db import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(
            select(TimelineEvent).where(TimelineEvent.session_id == sid)
        )
        events = result.scalars().all()
        event_types = {e.event_type for e in events}
        assert "agent.step.started" in event_types
        assert "agent.step.finished" in event_types


# ── Scenario 7: Security boundaries ────────────────────────────────

@pytest.mark.asyncio
async def test_security_boundaries(center, fake_node):
    """401/403/409/400 coverage."""
    node_id, token, auth = fake_node

    # 401: no auth
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_sec1_{_uid()}",
        "message_type": "node.hello", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {},
    })
    assert r.status_code == 401

    # 403: wrong node_id for token
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": f"msg_sec2_{_uid()}",
        "message_type": "node.hello", "trace_id": f"tr_{_uid()}",
        "node_id": "wrong-node-id", "timestamp": _ts(),
        "payload": {},
    }, headers=auth)
    assert r.status_code == 403

    # 409: duplicate message_id
    mid = f"msg_dup_{_uid()}"
    await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": mid,
        "message_type": "node.heartbeat", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"status": "online"},
    }, headers=auth)
    r = await center.post("/yqp/", json={
        "yqp_version": "0.1", "message_id": mid,
        "message_type": "node.heartbeat", "trace_id": f"tr_{_uid()}",
        "node_id": node_id, "timestamp": _ts(),
        "payload": {"status": "online"},
    }, headers=auth)
    assert r.status_code == 409
