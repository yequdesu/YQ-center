from types import SimpleNamespace

from yequ.application.transfer import _job_has_sender_ready
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


def test_croc_stderr_projects_percent() -> None:
    pct = _event_progress_pct(
        {
            "progress_source": "croc_stderr",
            "bytes_transferred": 92,
            "total_bytes": 100,
        }
    )

    assert pct == 92.0


def test_sender_ready_projection_survives_later_croc_progress() -> None:
    job = SimpleNamespace(progress_pct=None, progress_message=None, progress_detail=None)

    _apply_job_event_projection(
        job,
        "transfer_progress",
        {
            "progress_source": "croc_sender_ready",
            "phase": "sender_ready",
            "status": "running",
        },
    )
    _apply_job_event_projection(
        job,
        "transfer_progress",
        {
            "progress_source": "croc_stderr",
            "role": "sender",
            "progress_pct": 1,
            "status": "running",
        },
    )

    assert job.progress_detail["sender_ready"] is True
    assert job.progress_detail["progress_source"] == "croc_stderr"
    assert _job_has_sender_ready(job)


def test_sender_croc_progress_is_compatible_sender_ready_signal() -> None:
    job = SimpleNamespace(
        progress_detail={
            "role": "sender",
            "progress_source": "croc_stderr",
            "progress_pct": 1,
        }
    )

    assert _job_has_sender_ready(job)
