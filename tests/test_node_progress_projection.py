from types import SimpleNamespace

from yequ.application.transfer import (
    _job_has_sender_ready,
    _sender_ready_wait_sec,
    _transfer_job_lease_sec,
)
from yequ.services.node_service import _apply_job_event_projection, _event_progress_pct


def test_receiver_output_size_observation_does_not_project_percent() -> None:
    pct = _event_progress_pct(
        {
            "progress_source": "receiver_output_size_observation",
            "bytes_transferred": 100,
            "total_bytes": 100,
            "progress_pct": 100,
        }
    )

    assert pct is None


def test_yq_croc_bytes_progress_projects_percent() -> None:
    pct = _event_progress_pct(
        {
            "progress_source": "yq_croc_event",
            "event": "bytes_progress",
            "bytes_transferred": 92,
            "total_bytes": 100,
        }
    )

    assert pct == 92.0


def test_yq_sender_ready_projection_is_sticky() -> None:
    job = SimpleNamespace(progress_pct=None, progress_message=None, progress_detail=None)

    _apply_job_event_projection(
        job,
        "transfer_progress",
        {
            "progress_source": "yq_croc_event",
            "event": "sender_ready",
            "role": "sender",
            "status": "running",
        },
    )
    _apply_job_event_projection(
        job,
        "transfer_progress",
        {
            "progress_source": "yq_croc_event",
            "event": "bytes_progress",
            "role": "sender",
            "bytes_transferred": 1,
            "total_bytes": 100,
            "status": "running",
        },
    )

    assert job.progress_detail["sender_ready"] is True
    assert job.progress_detail["event"] == "bytes_progress"
    assert _job_has_sender_ready(job)


def test_sender_non_ready_progress_is_not_sender_ready_signal() -> None:
    job = SimpleNamespace(
        progress_detail={
            "role": "sender",
            "progress_source": "yq_croc_event",
            "event": "bytes_progress",
            "progress_pct": 1,
        }
    )

    assert not _job_has_sender_ready(job)


def test_transfer_sender_ready_wait_scales_beyond_short_lease_window() -> None:
    assert _sender_ready_wait_sec(3600) == 360.0
    assert _sender_ready_wait_sec(30) == 180.0
    assert _sender_ready_wait_sec(20_000) == 600.0


def test_transfer_job_lease_is_not_fixed_thirty_seconds() -> None:
    assert _transfer_job_lease_sec(3600) == 600
    assert _transfer_job_lease_sec(30) == 180
    assert _transfer_job_lease_sec(20_000) == 600
