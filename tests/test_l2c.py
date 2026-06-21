"""L2-C Maintenance Artifact + RollbackHint E2E tests."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import make_yqp_envelope
from yequ.models.invocation import Invocation
from yequ.models.job import Job
from yequ.models.maintenance_plan import MaintenanceArtifact


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _auto_complete_jobs(stop_event: asyncio.Event, node_id: str):
    """Background task: poll for running invocations and mark them succeeded.

    This simulates a daemon processing jobs so the executor's
    _wait_invocation_terminal() doesn't time out.
    """
    from yequ.db import async_session_factory as _asf
    seen: set[str] = set()
    while not stop_event.is_set():
        try:
            async with _asf() as db:
                result = await db.execute(
                    select(Invocation).where(
                        Invocation.target_node_id == node_id,
                        Invocation.status == "running",
                    )
                )
                for inv in result.scalars().all():
                    if inv.invocation_id in seen:
                        continue
                    seen.add(inv.invocation_id)

                    inv.status = "succeeded"
                    inv.result = {
                        "status": "running",
                        "found": True,
                        "service_name": "Spooler",
                        "display_name": "Print Spooler",
                        "message": f"Completed {inv.function_name}",
                    }
                    inv.finished_at = datetime.now(UTC)

                    # Also complete the job
                    job_result = await db.execute(
                        select(Job).where(Job.invocation_id == inv.invocation_id)
                    )
                    for job in job_result.scalars().all():
                        job.status = "succeeded"
                        job.finished_at = datetime.now(UTC)
                        job.result = inv.result

                await db.commit()
        except Exception:
            pass
        await asyncio.sleep(0.1)


async def _auto_fail_jobs(stop_event: asyncio.Event, node_id: str):
    """Background task: mark invocations as failed to simulate repair failure."""
    from yequ.db import async_session_factory as _asf
    seen: set[str] = set()
    while not stop_event.is_set():
        try:
            async with _asf() as db:
                result = await db.execute(
                    select(Invocation).where(
                        Invocation.target_node_id == node_id,
                        Invocation.status == "running",
                    )
                )
                for inv in result.scalars().all():
                    if inv.invocation_id in seen:
                        continue
                    seen.add(inv.invocation_id)

                    inv.status = "failed"
                    inv.finished_at = datetime.now(UTC)

                    job_result = await db.execute(
                        select(Job).where(Job.invocation_id == inv.invocation_id)
                    )
                    for job in job_result.scalars().all():
                        job.status = "failed"
                        job.error_code = "EXEC_FAILED"
                        job.error_message = f"Simulated failure for {inv.function_name}"
                        job.finished_at = datetime.now(UTC)

                await db.commit()
        except Exception:
            pass
        await asyncio.sleep(0.1)


@pytest.fixture
async def l2c_setup(client: AsyncClient):
    """Setup: provision node + register L1 read + L2 write capabilities."""
    node_id = f"node-{_uid()}"
    token = f"tok-{_uid()}"
    auth = {"Authorization": f"Bearer {token}"}

    await client.post("/admin/nodes", json={
        "node_id": node_id, "node_name": f"L2C-{node_id}", "token": token,
    })
    await client.post("/yqp/", json=make_yqp_envelope("node.hello", node_id, {
        "daemon_version": "0.1.0",
    }), headers=auth)
    await client.post("/yqp/", json=make_yqp_envelope(
        "node.register_capabilities", node_id,
        payload={"plugins": [{
            "plugin_id": "test.plugin", "plugin_version": "1.0.0",
            "functions": [
                {"name": "system.metrics.snapshot", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
                {"name": "system.info", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
                {"name": "system.service.status", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}, "output_schema": {"type": "object"}, "risk": "safe", "effect": "read", "timeout_sec": 5, "idempotency": "idempotent"},
                {"name": "system.service.ensure_running", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}, "output_schema": {"type": "object"}, "risk": "maintenance", "effect": "write", "timeout_sec": 30, "idempotency": "non_idempotent"},
            ],
            "signals": [],
        }]},
    ), headers=auth)

    return node_id, token, auth


async def _run_with_completer(
    client: AsyncClient,
    plan_id: str,
    node_id: str,
    approval_id: str = "",
    fail_mode: str | None = None,  # None = succeed, "repair" = fail repair step
):
    """Run a plan with a background job completer.

    fail_mode=None: all jobs succeed
    fail_mode="repair": repair step fails
    fail_mode="verify": verify step fails
    """
    stop = asyncio.Event()

    if fail_mode == "repair":
        completer = asyncio.create_task(_auto_fail_jobs(stop, node_id))
    elif fail_mode == "verify":
        # Complex: succeed check+repair, fail verify
        # We'll handle this with a custom completer
        async def _fail_verify_only(stop_event, nid):
            from yequ.db import async_session_factory as _asf
            seen: set[str] = set()
            fail_verify: set[str] = set()
            while not stop_event.is_set():
                try:
                    async with _asf() as db:
                        result = await db.execute(
                            select(Invocation).where(
                                Invocation.target_node_id == nid,
                                Invocation.status == "running",
                            )
                        )
                        for inv in result.scalars().all():
                            if inv.invocation_id in seen:
                                continue

                            # Check if this is the verify step (3rd invocation)
                            # Check step = 1st, repair = 2nd, verify = 3rd
                            if len(seen) >= 2 and inv.invocation_id not in fail_verify:
                                # This is the verify step — mark it to fail
                                fail_verify.add(inv.invocation_id)

                            seen.add(inv.invocation_id)

                            if inv.invocation_id in fail_verify:
                                inv.status = "failed"
                                inv.finished_at = datetime.now(UTC)
                                job_result = await db.execute(
                                    select(Job).where(Job.invocation_id == inv.invocation_id)
                                )
                                for job in job_result.scalars().all():
                                    job.status = "failed"
                                    job.error_code = "VERIFY_FAILED"
                                    job.error_message = "Verification after repair failed"
                                    job.finished_at = datetime.now(UTC)
                            else:
                                inv.status = "succeeded"
                                inv.result = {
                                    "status": "running",
                                    "found": True,
                                    "service_name": "Spooler",
                                    "message": f"Completed {inv.function_name}",
                                }
                                inv.finished_at = datetime.now(UTC)
                                job_result = await db.execute(
                                    select(Job).where(Job.invocation_id == inv.invocation_id)
                                )
                                for job in job_result.scalars().all():
                                    job.status = "succeeded"
                                    job.finished_at = datetime.now(UTC)
                                    job.result = inv.result
                        await db.commit()
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        completer = asyncio.create_task(_fail_verify_only(stop, node_id))
    else:
        completer = asyncio.create_task(_auto_complete_jobs(stop, node_id))

    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id} if approval_id else {},
        )
        return r
    finally:
        stop.set()
        await completer


# ── Healthy branch tests ──


@pytest.mark.asyncio
async def test_healthy_branch_check_only(client: AsyncClient, l2c_setup):
    """Healthy service: check step only → check_result, no before/after/rollback_hint."""
    node_id, token, auth = l2c_setup

    # Create a check-only plan
    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check Spooler status", "target_node_id": node_id,
        "steps": [{
            "function_name": "system.service.status",
            "input": {"name": "Spooler"},
            "kind": "check",
            "condition": "always",
        }],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Run
    r = await _run_with_completer(client, plan_id, node_id)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "succeeded"

    # Get artifacts
    r = await client.get(f"/admin/maintenance/runs/{data['run_id']}/artifacts")
    assert r.status_code == 200
    art_data = r.json()
    assert art_data["run_id"] == data["run_id"]

    kinds = {a["kind"] for a in art_data["artifacts"]}
    assert "check_result" in kinds
    assert "before" not in kinds
    assert "after" not in kinds
    assert "rollback_hint" not in kinds
    assert "verify_result" not in kinds
    assert "error" not in kinds

    # check_result should contain service info
    check = [a for a in art_data["artifacts"] if a["kind"] == "check_result"][0]
    assert check["data"] is not None
    assert check["summary"] is not None


@pytest.mark.asyncio
async def test_healthy_branch_repair_skipped(client: AsyncClient, l2c_setup):
    """Healthy Spooler: check+repair+verify plan → repair skipped, no before/after/rollback.

    Because the check returns status=running (healthy), the if_previous_unhealthy
    condition causes repair to be skipped.
    """
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check and repair Spooler if needed", "target_node_id": node_id,
        "steps": [
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "check", "condition": "always", "seq": 1,
            },
            {
                "function_name": "system.service.ensure_running",
                "input": {"name": "Spooler"},
                "kind": "repair", "condition": "if_previous_unhealthy",
                "depends_on": [1], "requires_approval": True,
                "rollback_hint": {
                    "action": "restore_service_state",
                    "target_type": "windows_service",
                    "service_name": "Spooler",
                    "rollback_function": "system.service.ensure_state",
                    "requires_approval": True,
                },
            },
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "verify", "condition": "after_repair",
                "depends_on": [2],
            },
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Approve
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    assert r.status_code == 200
    approval_id = r.json()["approval_id"]
    assert approval_id

    # Run with auto-completer (check returns status=running → repair skipped)
    r = await _run_with_completer(client, plan_id, node_id, approval_id)
    assert r.status_code == 200
    data = r.json()

    # Run should succeed (check succeeded, repair skipped, verify succeeded)
    assert data["status"] == "succeeded", f"Got status={data['status']}, steps={data.get('steps')}"

    # Check step statuses
    steps = data.get("steps", [])
    step_statuses = {s["kind"]: s["status"] for s in steps}
    assert step_statuses.get("check") == "succeeded"
    assert step_statuses.get("repair") == "skipped"
    assert step_statuses.get("verify") == "succeeded"

    # Artifacts — should NOT have before/after/rollback_hint
    r = await client.get(f"/admin/maintenance/runs/{data['run_id']}/artifacts")
    art_data = r.json()
    kinds = {a["kind"] for a in art_data["artifacts"]}
    assert "check_result" in kinds
    assert "verify_result" in kinds
    assert "before" not in kinds, "before artifact should not exist when repair skipped"
    assert "after" not in kinds, "after artifact should not exist when repair skipped"
    assert "rollback_hint" not in kinds, "rollback_hint should not exist when repair skipped"

    # Run detail
    r = await client.get(f"/admin/maintenance/runs/{data['run_id']}")
    assert r.status_code == 200
    run_detail = r.json()
    assert run_detail["rollback_recommended"] is False
    assert run_detail["rollback_hints"] == []
    assert "artifact_summary" in run_detail
    assert run_detail["artifact_summary"]["total"] > 0


# ── Unhealthy branch tests ──


@pytest.mark.asyncio
async def test_unhealthy_branch_full_repair(client: AsyncClient, l2c_setup):
    """Unhealthy Spooler: repair executes → before/after/rollback_hint artifacts."""
    node_id, token, auth = l2c_setup

    # Create check+repair+verify plan
    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check and fix Spooler", "target_node_id": node_id,
        "steps": [
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "check", "condition": "always", "seq": 1,
            },
            {
                "function_name": "system.service.ensure_running",
                "input": {"name": "Spooler"},
                "kind": "repair", "condition": "if_previous_unhealthy",
                "depends_on": [1], "requires_approval": True,
                "rollback_hint": {
                    "action": "restore_service_state",
                    "target_type": "windows_service",
                    "service_name": "Spooler",
                    "rollback_function": "system.service.ensure_state",
                    "requires_approval": True,
                },
            },
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "verify", "condition": "after_repair",
                "depends_on": [2],
            },
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Approve
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    # Use custom completer that returns unhealthy for check step
    stop = asyncio.Event()

    async def _unhealthy_completer(stop_event, nid):
        """First invocation (check) returns service not running."""
        from yequ.db import async_session_factory as _asf
        check_done = False
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if not check_done:
                            # Check step: return unhealthy
                            inv.status = "succeeded"
                            inv.result = {
                                "status": "stopped",
                                "found": False,
                                "service_name": "Spooler",
                                "display_name": "Print Spooler",
                                "message": "Service is not running",
                            }
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                            check_done = True
                        else:
                            # Repair + verify: return succeeded
                            inv.status = "succeeded"
                            inv.result = {
                                "status": "running",
                                "found": True,
                                "service_name": "Spooler",
                                "message": f"Completed {inv.function_name}",
                            }
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_unhealthy_completer(stop, node_id))

    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        assert r.status_code == 200
        data = r.json()
    finally:
        stop.set()
        await completer

    # All steps should succeed
    assert data["status"] == "succeeded", f"Status: {data['status']}, steps: {data.get('steps')}"
    steps = data.get("steps", [])
    step_statuses = {s["kind"]: s["status"] for s in steps}
    assert step_statuses.get("check") == "succeeded"
    assert step_statuses.get("repair") == "succeeded", f"Repair status: {step_statuses.get('repair')}"
    assert step_statuses.get("verify") == "succeeded"

    # Artifacts should include all
    r = await client.get(f"/admin/maintenance/runs/{data['run_id']}/artifacts")
    art_data = r.json()
    kinds = {a["kind"] for a in art_data["artifacts"]}
    assert "check_result" in kinds
    assert "before" in kinds, f"before artifact should exist when repair executes. Got: {kinds}"
    assert "after" in kinds
    assert "verify_result" in kinds
    assert "rollback_hint" in kinds

    # Run detail
    r = await client.get(f"/admin/maintenance/runs/{data['run_id']}")
    run_detail = r.json()
    assert run_detail["rollback_recommended"] is False
    assert run_detail["artifact_summary"]["total"] >= 5


# ── Repair failed test ──


@pytest.mark.asyncio
async def test_repair_failed_rollback_recommended(client: AsyncClient, l2c_setup):
    """Repair step fails → rollback_recommended, error+rollback_hint artifacts."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Fix Spooler", "target_node_id": node_id,
        "steps": [
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "check", "condition": "always", "seq": 1,
            },
            {
                "function_name": "system.service.ensure_running",
                "input": {"name": "Spooler"},
                "kind": "repair", "condition": "if_previous_unhealthy",
                "depends_on": [1], "requires_approval": True,
                "rollback_hint": {
                    "action": "restore_service_state",
                    "target_type": "windows_service",
                    "service_name": "Spooler",
                    "rollback_function": "system.service.ensure_state",
                    "requires_approval": True,
                },
            },
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    # Approve
    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    # Run with repair failure — use unhealthy check + fail repair
    stop = asyncio.Event()

    async def _repair_fail_completer(stop_event, nid):
        """Check returns unhealthy, repair fails."""
        from yequ.db import async_session_factory as _asf
        check_done = False
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if not check_done:
                            inv.status = "succeeded"
                            inv.result = {
                                "status": "stopped",
                                "found": False,
                                "service_name": "Spooler",
                                "message": "Service is not running",
                            }
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                            check_done = True
                        else:
                            inv.status = "failed"
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "failed"
                                job.error_code = "REPAIR_FAILED"
                                job.error_message = "Could not start service Spooler"
                                job.finished_at = datetime.now(UTC)
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_repair_fail_completer(stop, node_id))

    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        assert r.status_code == 200
        data = r.json()
    finally:
        stop.set()
        await completer

    # Run should be rollback_recommended
    assert data["status"] == "rollback_recommended", f"Got status={data['status']}"
    run_id = data["run_id"]

    # Artifacts
    r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts")
    art_data = r.json()
    kinds = {a["kind"] for a in art_data["artifacts"]}
    assert "check_result" in kinds
    assert "before" in kinds
    assert "error" in kinds, f"error artifact missing. Got kinds: {kinds}"
    assert "rollback_hint" in kinds

    # Error artifact should have structured error data
    error_art = [a for a in art_data["artifacts"] if a["kind"] == "error"][0]
    assert error_art["data"]["error_code"] == "REPAIR_FAILED"
    assert "function_name" in error_art["data"]

    # Run detail
    r = await client.get(f"/admin/maintenance/runs/{run_id}")
    run_detail = r.json()
    assert run_detail["rollback_recommended"] is True
    assert len(run_detail["rollback_hints"]) >= 1

    # Timeline check for rollback.recommended event
    r = await client.get(f"/admin/timeline?approval_id={approval_id}")
    if r.status_code == 200:
        events = r.json()
        event_types = {e["event_type"] for e in events}
        assert "maintenance.rollback.recommended" in event_types, \
            f"Expected maintenance.rollback.recommended in timeline, got: {event_types}"
        assert "maintenance.artifact.created" in event_types


# ── Verify failed test ──


@pytest.mark.asyncio
async def test_verify_failed_rollback_recommended(client: AsyncClient, l2c_setup):
    """Verify step fails after repair succeeded → rollback_recommended."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check+fix+verify Spooler", "target_node_id": node_id,
        "steps": [
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "check", "condition": "always", "seq": 1,
            },
            {
                "function_name": "system.service.ensure_running",
                "input": {"name": "Spooler"},
                "kind": "repair", "condition": "if_previous_unhealthy",
                "depends_on": [1], "requires_approval": True,
                "rollback_hint": {
                    "action": "restore_service_state",
                    "target_type": "windows_service",
                    "service_name": "Spooler",
                    "rollback_function": "system.service.ensure_state",
                    "requires_approval": True,
                },
            },
            {
                "function_name": "system.service.status",
                "input": {"name": "Spooler"},
                "kind": "verify", "condition": "after_repair",
                "depends_on": [2],
            },
        ],
    })
    assert r.status_code == 201
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    stop = asyncio.Event()

    async def _verify_fail_completer(stop_event, nid):
        """Check unhealthy, repair succeeds, verify fails."""
        from yequ.db import async_session_factory as _asf
        inv_count = 0
        seen: set[str] = set()
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if inv.invocation_id in seen:
                            continue
                        seen.add(inv.invocation_id)
                        inv_count = len(seen)

                        if inv_count <= 2:
                            # Check + repair: succeed (check returns unhealthy)
                            inv.status = "succeeded"
                            inv.result = {
                                "status": "stopped" if inv_count == 1 else "running",
                                "found": inv_count != 1,
                                "service_name": "Spooler",
                                "message": f"Step {inv_count} completed",
                            }
                            inv.finished_at = datetime.now(UTC)
                        else:
                            # Verify: fail
                            inv.status = "failed"
                            inv.finished_at = datetime.now(UTC)

                        job_result = await db.execute(
                            select(Job).where(Job.invocation_id == inv.invocation_id)
                        )
                        for job in job_result.scalars().all():
                            if inv.status == "failed":
                                job.status = "failed"
                                job.error_code = "VERIFY_FAILED"
                                job.error_message = "Service still not running after repair"
                            else:
                                job.status = "succeeded"
                                job.result = inv.result
                            job.finished_at = datetime.now(UTC)
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_verify_fail_completer(stop, node_id))

    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        data = r.json()
    finally:
        stop.set()
        await completer

    assert data["status"] == "rollback_recommended", f"Status: {data['status']}"
    run_id = data["run_id"]

    # Artifacts
    r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts")
    art_data = r.json()
    kinds = {a["kind"] for a in art_data["artifacts"]}
    assert "before" in kinds
    assert "after" in kinds, f"after should exist (repair succeeded). Got: {kinds}"
    assert "error" in kinds
    assert "rollback_hint" in kinds

    # Run detail
    r = await client.get(f"/admin/maintenance/runs/{run_id}")
    run_detail = r.json()
    assert run_detail["rollback_recommended"] is True


# ── Artifact API filter tests ──


@pytest.mark.asyncio
async def test_artifact_filter_by_kind(client: AsyncClient, l2c_setup):
    """Filter artifacts by kind parameter."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check Spooler", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "check", "condition": "always"},
            {"function_name": "system.service.ensure_running", "input": {"name": "Spooler"},
             "kind": "repair", "condition": "if_previous_unhealthy", "depends_on": [1],
             "requires_approval": True,
             "rollback_hint": {"action": "restore_service_state",
                               "target_type": "windows_service", "service_name": "Spooler",
                               "rollback_function": "system.service.ensure_state",
                               "requires_approval": True}},
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "verify", "condition": "after_repair", "depends_on": [2]},
        ],
    })
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    # Use unhealthy completer to trigger repair
    stop = asyncio.Event()

    async def _unhealthy(stop_event, nid):
        from yequ.db import async_session_factory as _asf
        check_done = False
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if not check_done:
                            inv.status = "succeeded"
                            inv.result = {"status": "stopped", "found": False, "service_name": "Spooler"}
                            inv.finished_at = datetime.now(UTC)
                            job_results = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_results.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                            check_done = True
                        else:
                            inv.status = "succeeded"
                            inv.result = {"status": "running", "found": True, "service_name": "Spooler"}
                            inv.finished_at = datetime.now(UTC)
                            job_results = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_results.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_unhealthy(stop, node_id))
    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        run_id = r.json()["run_id"]
    finally:
        stop.set()
        await completer

    # Filter by kind=error
    r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts?kind=error")
    art_data = r.json()
    for a in art_data["artifacts"]:
        assert a["kind"] == "error"

    # Filter by kind=check_result
    r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts?kind=check_result")
    art_data = r.json()
    assert len(art_data["artifacts"]) >= 1
    for a in art_data["artifacts"]:
        assert a["kind"] == "check_result"

    # Filter by step_id (verify step)
    # Get step IDs from run detail
    r = await client.get(f"/admin/maintenance/runs/{run_id}")
    run_detail = r.json()
    verify_step = [s for s in run_detail["steps"] if s["kind"] == "verify"]
    if verify_step:
        step_id = verify_step[0]["step_id"]
        r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts?step_id={step_id}")
        art_data = r.json()
        for a in art_data["artifacts"]:
            assert a["step_id"] == step_id


@pytest.mark.asyncio
async def test_artifacts_ordered_by_created_at_asc(client: AsyncClient, l2c_setup):
    """Artifacts are returned in created_at ASC order."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check Spooler", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "check", "condition": "always"},
        ],
    })
    plan_id = r.json()["plan_id"]

    r = await _run_with_completer(client, plan_id, node_id)
    run_id = r.json()["run_id"]

    r = await client.get(f"/admin/maintenance/runs/{run_id}/artifacts")
    art_data = r.json()
    artifacts = art_data["artifacts"]
    if len(artifacts) > 1:
        timestamps = [a["created_at"] for a in artifacts]
        assert timestamps == sorted(timestamps), f"Artifacts not sorted ASC: {timestamps}"


# ── Timeline by approval_id test ──


@pytest.mark.asyncio
async def test_timeline_by_approval_id_includes_artifact_events(client: AsyncClient, l2c_setup):
    """Timeline by approval_id includes maintenance.artifact.created events."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Check Spooler", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "check", "condition": "always"},
        ],
    })
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    r = await _run_with_completer(client, plan_id, node_id, approval_id)
    run_id = r.json()["run_id"]

    # Timeline by approval_id
    r = await client.get(f"/admin/timeline?approval_id={approval_id}")
    assert r.status_code == 200
    events = r.json()
    event_types = {e["event_type"] for e in events}

    # Should include approval chain
    assert "approval.requested" in event_types or "approval.approved" in event_types
    # Should include maintenance events
    assert "maintenance.plan.created" in event_types or "maintenance.run.started" in event_types
    # Should include artifact events
    assert "maintenance.artifact.created" in event_types, \
        f"Expected maintenance.artifact.created in timeline. Got types: {event_types}"


# ── Planner rollback_hint test ──


@pytest.mark.asyncio
async def test_agent_plan_includes_rollback_hint(client: AsyncClient, l2c_setup):
    """Agent-generated plan includes rollback_hint on repair steps."""
    node_id, token, auth = l2c_setup

    # Override provider for check_and_fix
    from yequ.api.routes.agent import register_provider, get_provider
    from yequ.agent.provider import AgentResult
    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.api.routes.agent import _default_functions

    # Always create a fresh provider to avoid cross-test contamination
    prov = FakeAgentProvider(provider_name="fake")
    for func in _default_functions():
        prov.add_function(func)
    prov.add_response("fix", AgentResult(success=True, output={"message": "check_and_fix"}))
    register_provider(prov)

    r = await client.post("/agent/plan", json={
        "session_id": f"sess_{_uid()}",
        "provider_name": "fake",
        "prompt": "check print spooler and fix if unhealthy",
        "target_node_id": node_id,
        "execution_mode": "auto",
    })
    assert r.status_code in (200, 201), f"Status: {r.status_code}, body: {r.text[:500]}"
    data = r.json()
    steps = data.get("steps", [])
    repair = [s for s in steps if s["kind"] == "repair"]
    assert len(repair) > 0, f"Expected a repair step in plan. Steps: {steps}"
    assert "rollback_hint" in repair[0], f"Repair step missing rollback_hint: {repair[0]}"
    hint = repair[0]["rollback_hint"]
    assert hint is not None
    assert "action" in hint
    assert hint["action"] in ("restore_service_state", "manual_review")


@pytest.mark.asyncio
async def test_rollback_hint_default_service_name(client: AsyncClient, l2c_setup):
    """Planner uses Spooler as default service name when prompt is vague."""
    node_id, token, auth = l2c_setup

    from yequ.agent.fake_provider import FakeAgentProvider
    from yequ.agent.provider import AgentResult
    from yequ.api.routes.agent import _default_functions, register_provider

    # Always create a fresh provider to avoid cross-test contamination
    prov = FakeAgentProvider(provider_name="fake")
    for func in _default_functions():
        prov.add_function(func)
    prov.add_response("fix", AgentResult(success=True, output={"message": "check_and_fix"}))
    register_provider(prov)

    # Vague prompt — no specific service mentioned
    r = await client.post("/agent/plan", json={
        "session_id": f"sess_{_uid()}",
        "provider_name": "fake",
        "prompt": "check and fix the service",
        "target_node_id": node_id,
        "execution_mode": "auto",
    })
    assert r.status_code in (200, 201), f"Status: {r.status_code}, body: {r.text[:500]}"
    data = r.json()
    repair = [s for s in data["steps"] if s["kind"] == "repair"]
    if repair:
        hint = repair[0].get("rollback_hint") or {}
        assert hint.get("service_name") == "Spooler", \
            f"Default service should be Spooler, got: {hint.get('service_name')}"


# ── Deduplication tests: each terminal event appears exactly once ──


async def _count_timeline_events(run_id: str, event_type: str, client: AsyncClient) -> int:
    """Count how many timeline events of a given type reference a run_id."""
    # Query global timeline and filter by event_type + data.run_id
    r = await client.get("/admin/timeline?limit=500")
    if r.status_code != 200:
        return 0
    count = 0
    for e in r.json():
        if e["event_type"] != event_type:
            continue
        data = e.get("data") or {}
        if data.get("run_id") == run_id:
            count += 1
    return count


@pytest.mark.asyncio
async def test_repair_failed_rollback_recommended_once(client: AsyncClient, l2c_setup):
    """Repair failure writes maintenance.rollback.recommended exactly once."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Test dedup repair fail", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "check", "condition": "always", "seq": 1},
            {"function_name": "test.maintenance.repair_fail", "input": {"name": "Spooler"},
             "kind": "repair", "condition": "if_previous_unhealthy", "depends_on": [1],
             "requires_approval": True,
             "rollback_hint": {"action": "restore_service_state",
                               "target_type": "windows_service", "service_name": "Spooler",
                               "rollback_function": "system.service.ensure_state",
                               "requires_approval": True}},
        ],
    })
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    # Use unhealthy check + test repair fail (auto-fails in executor)
    stop = asyncio.Event()

    async def _completer(stop_event, nid):
        from yequ.db import async_session_factory as _asf
        check_done = False
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if not check_done:
                            inv.status = "succeeded"
                            inv.result = {"status": "stopped", "found": False,
                                          "service_name": "Spooler"}
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                            check_done = True
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_completer(stop, node_id))
    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        run_id = r.json()["run_id"]
    finally:
        stop.set()
        await completer

    assert r.json()["status"] == "rollback_recommended"

    # Count maintenance.rollback.recommended events for this run_id
    rb_count = await _count_timeline_events(run_id, "maintenance.rollback.recommended", client)
    assert rb_count == 1, f"Expected 1 maintenance.rollback.recommended, got {rb_count}"

    # Count maintenance.run.failed events for this run_id
    failed_count = await _count_timeline_events(run_id, "maintenance.run.failed", client)
    assert failed_count == 1, f"Expected 1 maintenance.run.failed, got {failed_count}"


@pytest.mark.asyncio
async def test_verify_failed_rollback_recommended_once(client: AsyncClient, l2c_setup):
    """Verify failure writes maintenance.rollback.recommended exactly once."""
    node_id, token, auth = l2c_setup

    r = await client.post("/admin/maintenance/plans", json={
        "goal": "Test dedup verify fail", "target_node_id": node_id,
        "steps": [
            {"function_name": "system.service.status", "input": {"name": "Spooler"},
             "kind": "check", "condition": "always", "seq": 1},
            {"function_name": "system.service.ensure_running", "input": {"name": "Spooler"},
             "kind": "repair", "condition": "if_previous_unhealthy", "depends_on": [1],
             "requires_approval": True,
             "rollback_hint": {"action": "restore_service_state",
                               "target_type": "windows_service", "service_name": "Spooler",
                               "rollback_function": "system.service.ensure_state",
                               "requires_approval": True}},
            {"function_name": "test.maintenance.verify_fail", "input": {"name": "Spooler"},
             "kind": "verify", "condition": "after_repair", "depends_on": [2]},
        ],
    })
    plan_id = r.json()["plan_id"]

    r = await client.post(f"/admin/maintenance/plans/{plan_id}/approve")
    approval_id = r.json()["approval_id"]

    stop = asyncio.Event()

    async def _completer(stop_event, nid):
        from yequ.db import async_session_factory as _asf
        check_done = False
        while not stop_event.is_set():
            try:
                async with _asf() as db:
                    result = await db.execute(
                        select(Invocation).where(
                            Invocation.target_node_id == nid,
                            Invocation.status == "running",
                        )
                    )
                    for inv in result.scalars().all():
                        if not check_done:
                            inv.status = "succeeded"
                            inv.result = {"status": "stopped", "found": False,
                                          "service_name": "Spooler"}
                            inv.finished_at = datetime.now(UTC)
                            job_result = await db.execute(
                                select(Job).where(Job.invocation_id == inv.invocation_id)
                            )
                            for job in job_result.scalars().all():
                                job.status = "succeeded"
                                job.finished_at = datetime.now(UTC)
                                job.result = inv.result
                            check_done = True
                    await db.commit()
            except Exception:
                pass
            await asyncio.sleep(0.1)

    completer = asyncio.create_task(_completer(stop, node_id))
    try:
        r = await client.post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"approval_id": approval_id},
        )
        run_id = r.json()["run_id"]
    finally:
        stop.set()
        await completer

    assert r.json()["status"] == "rollback_recommended"

    rb_count = await _count_timeline_events(run_id, "maintenance.rollback.recommended", client)
    assert rb_count == 1, f"Expected 1 maintenance.rollback.recommended, got {rb_count}"

    failed_count = await _count_timeline_events(run_id, "maintenance.run.failed", client)
    assert failed_count <= 1, f"Expected at most 1 maintenance.run.failed, got {failed_count}"
