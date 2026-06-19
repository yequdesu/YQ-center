"""Unit tests for the job state machine."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.protocol import JobStatus
from yequ.services.job_state_machine import (
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    is_terminal,
    is_valid_transition,
    transition,
)


class TestValidTransitions:
    """All valid transitions defined in VALID_TRANSITIONS must succeed."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source,target", [
        (JobStatus.CREATED, JobStatus.QUEUED),
        (JobStatus.QUEUED, JobStatus.CLAIMED),
        (JobStatus.QUEUED, JobStatus.CANCELLED),
        (JobStatus.CLAIMED, JobStatus.RUNNING),
        (JobStatus.CLAIMED, JobStatus.TIMEOUT),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.CANCELLING),
        (JobStatus.RUNNING, JobStatus.TIMEOUT),
        (JobStatus.CANCELLING, JobStatus.CANCELLED),
        (JobStatus.CANCELLING, JobStatus.FAILED),
    ])
    async def test_valid_transition(
        self, db_session: AsyncSession, source, target,
    ):
        """Each valid transition should execute without error."""
        job = Job(
            job_id=f"job_sm_{source.value}_{target.value}",
            invocation_id="inv_sm_test",
            node_id="test-node",
            function_name="test.func",
            status=source.value,
        )
        db_session.add(job)
        await db_session.flush()

        await transition(db_session, job, target.value, node_id="test-node")
        assert job.status == target.value


class TestInvalidTransitions:
    """Invalid transitions must raise ValueError."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source,target", [
        (JobStatus.CREATED, JobStatus.RUNNING),       # skip queued
        (JobStatus.QUEUED, JobStatus.SUCCEEDED),      # skip claimed+running
        (JobStatus.CLAIMED, JobStatus.SUCCEEDED),     # skip running
        (JobStatus.RUNNING, JobStatus.QUEUED),        # backward
        (JobStatus.SUCCEEDED, JobStatus.FAILED),      # terminal -> anything
        (JobStatus.FAILED, JobStatus.SUCCEEDED),      # terminal -> anything
        (JobStatus.CANCELLED, JobStatus.RUNNING),     # terminal -> anything
        (JobStatus.TIMEOUT, JobStatus.RUNNING),       # terminal -> anything
    ])
    async def test_invalid_transition_raises(
        self, db_session: AsyncSession, source, target,
    ):
        """Invalid transitions must raise ValueError."""
        job = Job(
            job_id=f"job_inv_{source.value}_{target.value}",
            invocation_id="inv_sm_test",
            node_id="test-node",
            function_name="test.func",
            status=source.value,
        )
        db_session.add(job)
        await db_session.flush()

        with pytest.raises(ValueError, match="Invalid transition"):
            await transition(db_session, job, target.value, node_id="test-node")


class TestTerminalImmutability:
    """Terminal states must reject further transitions."""

    @pytest.mark.asyncio
    async def test_terminal_rejects_any_transition(self, db_session: AsyncSession):
        """Once in a terminal state, no more transitions allowed."""
        job = Job(
            job_id="job_term_test",
            invocation_id="inv_term",
            node_id="test-node",
            function_name="test.func",
            status=JobStatus.SUCCEEDED,
        )
        db_session.add(job)
        await db_session.flush()

        # Any attempt to transition a terminal job should fail
        # The state machine catches invalid transitions (no outgoing edges from terminal)
        with pytest.raises(ValueError, match="Invalid transition"):
            await transition(db_session, job, JobStatus.FAILED, node_id="test")


class TestHelpers:
    """Test is_valid_transition and is_terminal helpers."""

    def test_is_valid_transition(self):
        assert is_valid_transition("created", "queued") is True
        assert is_valid_transition("succeeded", "failed") is False
        assert is_valid_transition("running", "succeeded") is True

    def test_is_terminal(self):
        assert is_terminal("succeeded") is True
        assert is_terminal("failed") is True
        assert is_terminal("cancelled") is True
        assert is_terminal("timeout") is True
        assert is_terminal("running") is False
        assert is_terminal("queued") is False


class TestValidTransitionMap:
    """The VALID_TRANSITIONS dict must be complete and correct."""

    def test_all_statuses_have_transition_map(self):
        """Every JobStatus must have an entry in VALID_TRANSITIONS."""
        for status in JobStatus:
            assert status in VALID_TRANSITIONS, f"Missing {status}"

    def test_terminal_statuses_have_no_outgoing(self):
        """Terminal states must have empty target sets."""
        for status in TERMINAL_STATUSES:
            assert VALID_TRANSITIONS[status] == set()

    def test_non_terminal_statuses_have_outgoing(self):
        """Non-terminal states must have at least one outgoing transition."""
        for status in NON_TERMINAL_STATUSES:
            assert len(VALID_TRANSITIONS[status]) > 0
