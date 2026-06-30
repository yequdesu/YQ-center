from yequ.services.node_service import _event_progress_pct


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
