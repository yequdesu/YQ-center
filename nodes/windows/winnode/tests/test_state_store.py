from __future__ import annotations

import os
import tempfile
from pathlib import Path


def test_state_store_init():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            assert db_path.exists()
            summary = store.get_db_summary()
            assert summary["total_jobs"] == 0
        finally:
            store.close()


def test_upsert_and_get_job():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            store.upsert_job_result(
                job_id="job_001",
                status="succeeded",
                function_name="windows.exec.run",
                result={"cpu": 12},
            )
            job = store.get_job("job_001")
            assert job is not None
            assert job["job_id"] == "job_001"
            assert job["status"] == "succeeded"
            assert job["function_name"] == "windows.exec.run"
            assert job["reported_at"] is None
        finally:
            store.close()


def test_unreported_jobs():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            store.upsert_job_result("job_001", "succeeded")
            store.upsert_job_result("job_002", "failed")
            store.mark_reported("job_001")
            unreported = store.get_unreported()
            assert len(unreported) == 1
            assert unreported[0]["job_id"] == "job_002"
        finally:
            store.close()


def test_mark_reported():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            store.upsert_job_result("job_001", "succeeded")
            store.mark_reported("job_001")
            job = store.get_job("job_001")
            assert job["reported_at"] is not None
        finally:
            store.close()


def test_recent_jobs():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            for i in range(10):
                store.upsert_job_result(f"job_{i:03d}", "succeeded")
            jobs = store.get_recent_jobs(limit=5)
            assert len(jobs) == 5
        finally:
            store.close()


def test_daemon_state():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            store.set_daemon_state("last_heartbeat", "2026-06-21T00:00:00Z")
            store.set_daemon_state("center_status", "online")
            val = store.get_daemon_state("last_heartbeat")
            assert val == "2026-06-21T00:00:00Z"
            all_state = store.get_all_daemon_state()
            assert len(all_state) == 2
        finally:
            store.close()


def test_flush_reported():
    from node_win_client.state_store import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_state.sqlite3"
        store = StateStore(db_path)
        try:
            store.upsert_job_result("job_001", "succeeded")
            store.upsert_job_result("job_002", "failed")
            store.mark_reported("job_001")
            store.mark_reported("job_002")
            count = store.flush_reported(ttl_hours=0)
            assert count == 2
        finally:
            store.close()

