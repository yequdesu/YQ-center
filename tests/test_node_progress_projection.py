from yequ.application.transfer import (
    _job_has_receiver_ready,
    _receiver_ready_wait_sec,
)
from yequ.models.job import Job
from yequ.services.node_service import _apply_job_event_projection


def test_rclone_receiver_ready_projection() -> None:
    job = Job(
        job_id="job_1",
        invocation_id="inv_1",
        node_id="node_1",
        function_name="linux.transfer.rclone.receive",
        status="running",
    )

    _apply_job_event_projection(
        job,
        "job.progress",
        {
            "progress_source": "rclone_receiver_ready",
            "phase": "receiver_ready",
            "receiver_ready": True,
            "endpoint": {
                "host": "node.example",
                "port": 42981,
                "username": "yequ_trf_test",
            },
        },
    )

    assert job.progress_detail["receiver_ready"] is True
    assert job.progress_detail["phase"] == "receiver_ready"
    assert _job_has_receiver_ready(job)


def test_transfer_receiver_ready_wait_scales_beyond_short_lease_window() -> None:
    assert _receiver_ready_wait_sec(3600) == 360.0
    assert _receiver_ready_wait_sec(30) == 180.0
    assert _receiver_ready_wait_sec(20_000) == 600.0
