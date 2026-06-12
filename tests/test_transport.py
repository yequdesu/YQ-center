import json
import pytest
from yequ.transport.http_server import create_app
from yequ.storage.database import init_database
from yequ.registry.store import DeviceStore
from yequ.notify.base import NotifyRouter
from yequ.collector.runner import CollectorRunner
from starlette.testclient import TestClient


@pytest.fixture
def app(db_path, tmp_path):
    init_database(db_path)
    store = DeviceStore(db_path)
    notify = NotifyRouter(log_path=str(tmp_path / "gateway.log"))
    runner = CollectorRunner(db_path=db_path, device_store=store)

    # Pre-register a local device for testing
    store.register_device("testhost", is_local=True)

    return create_app(
        db_path=db_path,
        device_store=store,
        notify_router=notify,
        collector_runner=runner,
    )


@pytest.fixture
def client(app):
    return TestClient(app)


class TestHelloEndpoint:
    def test_registration_hello_returns_pending(self, client):
        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "registration",
            "device_id": "new-phone",
            "device_info": {"os": "Android 15"},
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["retry_after"] == 30

    def test_heartbeat_hello_with_valid_token(self, client, db_path):
        # Register device first
        from yequ.registry.store import DeviceStore
        store = DeviceStore(db_path)
        device = store.register_device("valid-device")

        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "heartbeat",
            "device_id": "valid-device",
            "token": device.token,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_heartbeat_hello_bad_token(self, client):
        response = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "heartbeat",
            "device_id": "unknown",
            "token": "badtoken",
        })

        assert response.status_code == 401

    def test_registration_retry_logic(self, client, db_path):
        # First registration
        client.post("/hello", json={
            "protocol": "yqp/1.0",
            "hello_type": "registration",
            "device_id": "retry-device",
            "device_info": {},
        })

        # Retries 1-10: still get retry_after=30
        for _ in range(10):
            resp = client.post("/hello", json={
                "protocol": "yqp/1.0",
                "hello_type": "registration",
                "device_id": "retry-device",
                "device_info": {},
            })
            data = resp.json()
            assert data["status"] == "pending"

        # 11th retry: should get longer retry_after
        resp = client.post("/hello", json={
            "protocol": "yqp/1.0",
            "hello_type": "registration",
            "device_id": "retry-device",
            "device_info": {},
        })
        data = resp.json()
        assert data["retry_after"] >= 60


class TestIngestEndpoint:
    def test_ingest_with_valid_token(self, client, db_path):
        store = DeviceStore(db_path)
        device = store.register_device("ingest-device")
        # Add and approve capability
        store.add_capability("ingest-device", {
            "name": "location",
            "display": "位置",
            "schema": {"type": "object"},
        })
        store.approve_capability("ingest-device", "location")

        response = client.post("/ingest", json={
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "msg-001",
            "device_id": "ingest-device",
            "token": device.token,
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {"lat": 31.23, "lng": 121.47},
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_ingest_bad_token(self, client):
        response = client.post("/ingest", json={
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "msg-001",
            "device_id": "bad-device",
            "token": "badtoken",
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {},
        })

        assert response.status_code == 401
