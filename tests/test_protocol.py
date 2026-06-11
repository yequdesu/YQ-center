import json
import pytest
from dataclasses import asdict
from yequ.protocol.messages import (
    HelloRegistration,
    HelloHeartbeat,
    Ingest,
    Ack,
    Command,
    HelloResponse,
    RegistrationResponse,
)


class TestHelloRegistration:
    def test_serialize_to_json(self):
        msg = HelloRegistration(
            device_id="pixel-8a",
            device_info={"os": "Android 15", "hostname": "pixel-8a"},
        )
        data = msg.to_dict()

        assert data["protocol"] == "yqp/1.0"
        assert data["message_type"] == "hello"
        assert data["hello_type"] == "registration"
        assert data["device_id"] == "pixel-8a"
        assert data["device_info"]["os"] == "Android 15"
        assert "token" not in data

    def test_deserialize_from_json(self):
        raw = json.dumps({
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "registration",
            "device_id": "pixel-8a",
            "device_info": {"os": "Android 15", "hostname": "pixel-8a"},
        })
        msg = HelloRegistration.from_json(raw)

        assert msg.device_id == "pixel-8a"
        assert msg.device_info["os"] == "Android 15"
        assert msg.hello_type == "registration"


class TestHelloHeartbeat:
    def test_serialize(self):
        msg = HelloHeartbeat(device_id="pixel-8a", token="secret123")
        data = msg.to_dict()

        assert data["hello_type"] == "heartbeat"
        assert data["token"] == "secret123"


class TestIngest:
    def test_serialize(self):
        msg = Ingest(
            message_id="abc-123",
            device_id="pixel-8a",
            token="secret123",
            timestamp="2026-06-12T10:30:00Z",
            capability="location",
            schema_version="v1",
            payload={"lat": 31.23, "lng": 121.47},
        )
        data = msg.to_dict()

        assert data["message_type"] == "ingest"
        assert data["capability"] == "location"
        assert data["payload"]["lat"] == 31.23

    def test_deserialize(self):
        raw = json.dumps({
            "protocol": "yqp/1.0",
            "message_type": "ingest",
            "message_id": "abc-123",
            "device_id": "pixel-8a",
            "token": "secret123",
            "timestamp": "2026-06-12T10:30:00Z",
            "capability": "location",
            "schema_version": "v1",
            "payload": {"lat": 31.23, "lng": 121.47},
        })
        msg = Ingest.from_json(raw)

        assert msg.message_id == "abc-123"
        assert msg.capability == "location"
        assert msg.payload["lat"] == 31.23

    def test_message_id_auto_generated(self):
        msg = Ingest(
            device_id="test",
            token="s",
            timestamp="2026-06-12T10:30:00Z",
            capability="cpu",
            schema_version="v1",
            payload={"pct": 50},
        )
        assert msg.message_id is not None
        assert len(msg.message_id) == 36  # UUID4


class TestAck:
    def test_ok_response(self):
        ack = Ack(message_id="abc-123", status="ok")
        data = ack.to_dict()

        assert data["message_type"] == "ack"
        assert data["status"] == "ok"
        assert data["pending_commands"] == []

    def test_with_commands(self):
        cmd = Command(action="set_interval", params={"capability": "location", "interval": 30})
        ack = Ack(message_id="abc-123", status="ok", pending_commands=[cmd])
        data = ack.to_dict()

        assert len(data["pending_commands"]) == 1
        assert data["pending_commands"][0]["action"] == "set_interval"


class TestHelloResponse:
    def test_pending(self):
        resp = HelloResponse(status="pending", retry_after=30)
        data = resp.to_dict()

        assert data["status"] == "pending"
        assert data["retry_after"] == 30

    def test_approved(self):
        resp = RegistrationResponse(
            status="approved",
            token="secret123",
            config={"collector": {"interval_seconds": 60}},
        )
        data = resp.to_dict()

        assert data["status"] == "approved"
        assert data["token"] == "secret123"
        assert data["config"]["collector"]["interval_seconds"] == 60
