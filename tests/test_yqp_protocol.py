"""Contract tests for all 9 YQP Node protocol endpoints."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.conftest import make_yqp_envelope


async def _create_queued_job(node_id: str, job_id: str = "job_test_001"):
    """Helper: insert a queued job into the DB."""
    from yequ.db import async_session_factory
    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    async with async_session_factory() as db:
        job = Job(
            job_id=job_id,
            invocation_id=f"inv_{job_id}",
            node_id=node_id,
            function_name="test.function",
            input_payload={},
            status=JobStatus.QUEUED,
            timeout_sec=30,
            lease_sec=10,
        )
        db.add(job)
        await db.commit()


# ── Auth & Security Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_auth_returns_401(client: AsyncClient):
    """Any YQP request without Authorization header must return 401."""
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            "any-node",
        ),
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client: AsyncClient):
    """Request with invalid Bearer token must return 401."""
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            "any-node",
        ),
        headers={"Authorization": "Bearer invalid-token-that-does-not-exist"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_node_id_returns_403(client: AsyncClient, provisioned_node):
    """node_id in envelope must match the token's bound node."""
    node, token = provisioned_node
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            "wrong-node-id",  # different from provisioned node_id
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_message_id_returns_409(client: AsyncClient, provisioned_node):
    """Duplicate message_id within dedup window must return 409."""
    node, token = provisioned_node
    env = make_yqp_envelope("node.hello", node.node_id, message_id="msg_dup_001")

    # First request -- should succeed
    resp1 = await client.post("/yqp/", json=env, headers={"Authorization": f"Bearer {token}"})
    assert resp1.status_code == 200

    # Second request with same message_id -- should be rejected
    resp2 = await client.post("/yqp/", json=env, headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_message_id_dedup_is_persisted(client: AsyncClient, provisioned_node, db_session):
    """YQP message_id replay protection is recorded in the database."""
    from sqlalchemy import select

    from yequ.models.yqp_message import YqpMessage

    node, token = provisioned_node
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope("node.hello", node.node_id, message_id="msg_db_dedup_001"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await db_session.rollback()
    result = await db_session.execute(
        select(YqpMessage).where(YqpMessage.message_id == "msg_db_dedup_001")
    )
    record = result.scalar_one()
    assert record.node_id == node.node_id
    assert record.message_type == "node.hello"
    assert record.expires_at is not None


@pytest.mark.asyncio
async def test_expired_yqp_message_cleanup_is_background_callable(db_session):
    from datetime import timedelta

    from sqlalchemy import select

    from yequ.models.yqp_message import YqpMessage
    from yequ.services.message_dedup import cleanup_expired_messages_once

    now = datetime.now(UTC)
    db_session.add_all(
        [
            YqpMessage(
                message_id="expired-yqp-message",
                node_id="node-a",
                message_type="node.heartbeat",
                received_at=now - timedelta(minutes=10),
                expires_at=now - timedelta(minutes=1),
            ),
            YqpMessage(
                message_id="live-yqp-message",
                node_id="node-a",
                message_type="node.heartbeat",
                received_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
        ]
    )
    await db_session.commit()

    deleted = await cleanup_expired_messages_once(now)

    assert deleted == 1
    await db_session.rollback()
    result = await db_session.execute(select(YqpMessage.message_id))
    assert set(result.scalars().all()) == {"live-yqp-message"}


@pytest.mark.asyncio
async def test_timestamp_skew_returns_400(client: AsyncClient, provisioned_node):
    """Timestamp outside allowed skew must return 400."""
    node, token = provisioned_node
    env = make_yqp_envelope("node.hello", node.node_id)
    # Set timestamp to 1 hour in the past
    env["timestamp"] = datetime(2020, 1, 1, tzinfo=UTC).isoformat()

    resp = await client.post("/yqp/", json=env, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


# ── node.hello Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hello_returns_accepted(client: AsyncClient, provisioned_node):
    """node.hello must return node.accepted with protocol parameters."""
    node, token = provisioned_node
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node.node_id,
            payload={
                "daemon_version": "0.1.0",
                "node_name": "Test Node",
                "role": ["compute"],
                "locality": "local",
                "platform": {"os": "linux", "arch": "x86_64"},
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["message_type"] == "node.accepted"
    assert "heartbeat_interval_sec" in data["payload"]
    assert "server_time" in data["payload"]
    assert data["payload"]["job_delivery_mode"] == "poll"


# ── node.heartbeat Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_succeeds(client: AsyncClient, provisioned_node):
    """node.heartbeat must succeed after hello."""
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}

    # Hello first
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node.node_id,
            payload={"daemon_version": "0.1.0"},
        ),
        headers=auth,
    )

    # Heartbeat
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.heartbeat",
            node.node_id,
            payload={"daemon_uptime_sec": 3600, "running_jobs": 0, "status": "online"},
        ),
        headers=auth,
    )

    assert resp.status_code == 200


# ── Capability Registration Tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_register_capabilities_full_snapshot(client: AsyncClient, provisioned_node):
    """Register capabilities, then re-register with fewer -- old ones deactivated."""
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}

    # First registration -- 2 capabilities (1 function + 1 signal)
    resp1 = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "test.plugin",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "test.plugin.func_a",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            }
                        ],
                        "signals": [
                            {
                                "name": "test.plugin.signal_a",
                                "scope": "node",
                                "ttl_sec": 15,
                                "value_schema": {"type": "number"},
                            }
                        ],
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert resp1.status_code == 200
    assert resp1.json()["payload"]["registered_count"] == 2

    # Second registration -- only 1 function, no signals (full snapshot)
    resp2 = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "test.plugin",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "test.plugin.func_b",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": "idempotent",
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert resp2.status_code == 200
    assert resp2.json()["payload"]["registered_count"] == 1  # only func_b active


@pytest.mark.asyncio
async def test_register_capabilities_rejects_invalid_idempotency(
    client: AsyncClient,
    provisioned_node,
):
    """Invalid manifest idempotency should be a schema error, not a DB 500."""
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "test.plugin",
                        "plugin_version": "1.0.0",
                        "functions": [
                            {
                                "name": "test.plugin.func_a",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "timeout_sec": 5,
                                "idempotency": ("idempotent_when_overwrite_and_artifact_unchanged"),
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "schema_invalid"
    assert detail["details"]["function_name"] == "test.plugin.func_a"
    assert detail["details"]["field"] == "plugins[0].functions[0].idempotency"


@pytest.mark.asyncio
async def test_register_capabilities_plugin_error(client: AsyncClient, provisioned_node):
    """Plugin with status=error should be recorded but not block registration."""
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "bad.plugin",
                        "plugin_version": "0.1.0",
                        "status": "error",
                        "error": {"code": "plugin_load_failed", "message": "init failed"},
                        "functions": [],
                        "signals": [],
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["registered_count"] == 0
    assert resp.json()["payload"]["failed_count"] == 1


# ── Signal Report Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_report_validates_schema(client: AsyncClient, provisioned_node, db_session):
    """Valid signals accepted, invalid ones rejected per value_schema."""
    node, token = provisioned_node
    auth = {"Authorization": f"Bearer {token}"}
    ts = datetime.now(UTC).isoformat()

    # Register a signal with value_schema (number 0-100)
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "sys.metrics",
                        "plugin_version": "1.0.0",
                        "functions": [],
                        "signals": [
                            {
                                "name": "cpu.usage",
                                "scope": "node",
                                "ttl_sec": 15,
                                "value_schema": {"type": "number", "minimum": 0, "maximum": 100},
                            }
                        ],
                    }
                ]
            },
        ),
        headers=auth,
    )

    # Report signals -- one valid, one invalid
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "signal.report",
            node.node_id,
            payload={
                "signals": [
                    {"name": "cpu.usage", "value": 42.5, "collected_at": ts, "ttl_sec": 15},
                    {"name": "cpu.usage", "value": 999, "collected_at": ts, "ttl_sec": 15},
                ]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["accepted"] == 1
    assert resp.json()["payload"]["rejected"] == 1

    from sqlalchemy import select

    from yequ.models.signal_state import SignalState

    await db_session.rollback()
    state_result = await db_session.execute(
        select(SignalState).where(
            SignalState.node_id == node.node_id,
            SignalState.signal_name == "cpu.usage",
        )
    )
    state = state_result.scalar_one()
    assert state.value == 42.5
    assert state.ttl_sec == 15
    assert state.freshness_status == "fresh"
    assert state.expires_at is not None


# ── Job Operation Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_poll_returns_available(client: AsyncClient, node_with_hello):
    """Polling with queued jobs should return job.available."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 2},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["message_type"] == "job.available"
    assert len(resp.json()["payload"]["jobs"]) == 1
    assert resp.json()["payload"]["jobs"][0]["job_id"] == "job_test_001"


@pytest.mark.asyncio
async def test_job_poll_empty_returns_empty(client: AsyncClient, node_with_hello):
    """Polling with no jobs should return job.empty with an empty jobs list."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 2},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["message_type"] == "job.empty"
    assert resp.json()["node_id"] == node.node_id
    assert resp.json()["payload"]["jobs"] == []


@pytest.mark.asyncio
async def test_job_accepted_transitions_to_running(client: AsyncClient, node_with_hello):
    """Accepting a claimed job transitions it to running."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    # Poll to claim the job
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )

    # Accept
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_test_001"},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_job_event_projects_progress_to_job_read_model(
    client: AsyncClient,
    node_with_hello,
    db_session,
):
    """job.event keeps Timeline as event log and updates Job progress projection."""
    from sqlalchemy import select

    from yequ.models.job import Job

    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_test_001"},
        ),
        headers=auth,
    )

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.event",
            node.node_id,
            payload={
                "job_id": "job_test_001",
                "event_type": "transfer_progress",
                "sequence": 3,
                "data": {
                    "transfer_id": "trf_test",
                    "role": "sender",
                    "status": "running",
                    "bytes_transferred": 25,
                    "total_bytes": 100,
                },
            },
        ),
        headers=auth,
    )

    assert resp.status_code == 200
    assert resp.json()["payload"]["event_type"] == "transfer_progress"

    await db_session.rollback()
    job_result = await db_session.execute(select(Job).where(Job.job_id == "job_test_001"))
    job = job_result.scalar_one()
    assert job.status == "running"
    assert job.progress_pct == 25.0
    assert job.progress_message == "sender running"
    assert job.progress_detail
    assert job.progress_detail["bytes_transferred"] == 25.0
    assert job.progress_detail["total_bytes"] == 100.0
    assert job.progress_detail["transfer_id"] == "trf_test"
    assert job.progress_detail["last_progress_at"]


@pytest.mark.asyncio
async def test_job_finished_terminal(client: AsyncClient, node_with_hello):
    """Finishing a running job enters terminal state."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    # Poll + Accept
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_test_001"},
        ),
        headers=auth,
    )

    # Finish
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            node.node_id,
            payload={"job_id": "job_test_001", "status": "succeeded", "output": {"ok": True}},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_job_finished_rejects_non_running_job(client: AsyncClient, node_with_hello):
    """Finishing a queued job directly must be rejected by the state machine."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            node.node_id,
            payload={"job_id": "job_test_001", "status": "succeeded", "output": {"ok": True}},
        ),
        headers=auth,
    )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_job_double_finish_is_idempotent(client: AsyncClient, node_with_hello):
    """Repeating the same terminal report is accepted for lost-response recovery."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    # Poll + Accept + Finish
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_test_001"},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            node.node_id,
            payload={"job_id": "job_test_001", "status": "succeeded", "output": {}},
        ),
        headers=auth,
    )

    # Double finish
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.finished",
            node.node_id,
            payload={"job_id": "job_test_001", "status": "succeeded", "output": {}},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    payload = resp.json()["payload"]
    assert payload["status"] == "already_terminal"
    assert payload["center_status"] == "succeeded"


@pytest.mark.asyncio
async def test_job_lease_renew_accepted(client: AsyncClient, node_with_hello):
    """Lease renewal for running job should be accepted."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id)

    # Poll + Accept (makes it running)
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_test_001"},
        ),
        headers=auth,
    )

    # Lease renew
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.lease_renew",
            node.node_id,
            payload={"job_id": "job_test_001", "lease_extend_sec": 15},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["status"] == "accepted"
    assert "lease_expires_at" in resp.json()["payload"]


# ── Reconcile Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_unknown_job_returns_forget(client: AsyncClient, node_with_hello):
    """Reconcile an unknown job must return forget action."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.reconcile_jobs",
            node.node_id,
            payload={"known_jobs": [{"job_id": "job_unknown", "local_status": "running"}]},
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json()["payload"]["actions"][0]["action"] == "forget"


@pytest.mark.asyncio
async def test_reconcile_running_returns_continue(client: AsyncClient, node_with_hello):
    """Reconcile a running job that Center also has as running returns continue."""
    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    await _create_queued_job(node.node_id, "job_rec_001")

    # Poll to claim it
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.poll",
            node.node_id,
            payload={"capacity": 1},
        ),
        headers=auth,
    )
    # Accept to make it running
    await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "job.accepted",
            node.node_id,
            payload={"job_id": "job_rec_001"},
        ),
        headers=auth,
    )

    # Reconcile
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.reconcile_jobs",
            node.node_id,
            payload={
                "known_jobs": [
                    {"job_id": "job_rec_001", "local_status": "running"},
                ]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    action = resp.json()["payload"]["actions"][0]
    assert action["action"] == "continue"


@pytest.mark.asyncio
async def test_reconcile_center_cancelling_returns_cancel(
    client: AsyncClient,
    node_with_hello,
    db_session,
):
    """A daemon running job must stop when Center has moved it to cancelling."""
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    job = Job(
        job_id="job_rec_cancelling_001",
        invocation_id="inv_rec_cancelling_001",
        node_id=node.node_id,
        function_name="test.function",
        input_payload={},
        status=JobStatus.CANCELLING,
        timeout_sec=30,
        lease_sec=10,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.reconcile_jobs",
            node.node_id,
            payload={
                "known_jobs": [{"job_id": "job_rec_cancelling_001", "local_status": "running"}]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    action = resp.json()["payload"]["actions"][0]
    assert action["action"] == "cancel"
    assert action["reason"] == "center_cancelling"

    await db_session.rollback()
    result = await db_session.execute(select(Job).where(Job.job_id == "job_rec_cancelling_001"))
    stored = result.scalar_one()
    assert stored.status == JobStatus.CANCELLING


@pytest.mark.asyncio
async def test_reconcile_center_terminal_discards_late_daemon_result(
    client: AsyncClient,
    node_with_hello,
    db_session,
):
    """When Center is terminal, reconcile must not accept a late daemon result."""
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    node, token = node_with_hello
    auth = {"Authorization": f"Bearer {token}"}
    job = Job(
        job_id="job_rec_terminal_001",
        invocation_id="inv_rec_terminal_001",
        node_id=node.node_id,
        function_name="test.function",
        input_payload={},
        output={"center": True},
        status=JobStatus.SUCCEEDED,
        timeout_sec=30,
        lease_sec=10,
    )
    db_session.add(job)
    await db_session.commit()

    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.reconcile_jobs",
            node.node_id,
            payload={
                "known_jobs": [
                    {
                        "job_id": "job_rec_terminal_001",
                        "local_status": "failed",
                        "output": {"daemon": True},
                        "error_code": "daemon_late_failure",
                    }
                ]
            },
        ),
        headers=auth,
    )
    assert resp.status_code == 200
    action = resp.json()["payload"]["actions"][0]
    assert action["action"] == "discard_result"
    assert action["center_status"] == "succeeded"
    assert action["local_status"] == "failed"

    await db_session.rollback()
    result = await db_session.execute(select(Job).where(Job.job_id == "job_rec_terminal_001"))
    stored = result.scalar_one()
    assert stored.status == JobStatus.SUCCEEDED
    assert stored.output == {"center": True}
