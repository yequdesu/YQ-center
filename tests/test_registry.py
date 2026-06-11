import json
import pytest
from yequ.registry.models import Device, DeviceStatus, Capability
from yequ.registry.store import DeviceStore
from yequ.storage.database import init_database


class TestDevice:
    def test_create_device(self):
        device = Device(
            device_id="test-device",
            token="tok123",
            labels={"role": "phone"},
        )

        assert device.device_id == "test-device"
        assert device.status == DeviceStatus.ACTIVE
        assert device.is_local is False

    def test_create_local_device(self):
        device = Device(
            device_id="local-host",
            token="localtok",
            is_local=True,
        )

        assert device.is_local is True

    def test_to_row(self):
        device = Device(
            device_id="dev1",
            token="t1",
            labels={"role": "server", "location": "home"},
        )
        row = device.to_row()

        assert row["device_id"] == "dev1"
        assert json.loads(row["labels_json"]) == {"role": "server", "location": "home"}

    def test_from_row(self):
        row = {
            "device_id": "dev1",
            "token": "t1",
            "labels_json": '{"role": "phone"}',
            "status": "active",
            "is_local": 0,
            "last_hello_at": "2026-06-12T10:00:00Z",
            "created_at": "2026-06-12T00:00:00Z",
            "updated_at": "2026-06-12T10:00:00Z",
        }
        device = Device.from_row(row)

        assert device.device_id == "dev1"
        assert device.labels == {"role": "phone"}
        assert device.last_hello_at == "2026-06-12T10:00:00Z"

    def test_touch_hello(self):
        device = Device(device_id="d1", token="t1")
        device.touch_hello()

        assert device.last_hello_at is not None
        assert device.status == DeviceStatus.ACTIVE


class TestCapability:
    def test_from_declaration(self):
        decl = {
            "name": "location",
            "display": "设备位置",
            "schema_version": "v1",
            "data_type": "snapshot",
            "interval": 300,
            "schema": {"type": "object", "properties": {"lat": {"type": "number"}}},
            "retention_days": 30,
        }
        cap = Capability.from_declaration("dev1", decl)

        assert cap.device_id == "dev1"
        assert cap.name == "location"
        assert cap.data_type == "snapshot"
        assert cap.interval_seconds == 300
        assert cap.is_approved is False

    def test_default_values(self):
        decl = {"name": "cpu", "display": "CPU Usage"}
        cap = Capability.from_declaration("dev1", decl)

        assert cap.data_type == "snapshot"
        assert cap.interval_seconds == 60
        assert cap.schema_version == "v1"


class TestDeviceStore:
    @pytest.fixture
    def store(self, db_path):
        init_database(db_path)
        return DeviceStore(db_path)

    def test_register_device(self, store):
        device = store.register_device(
            device_id="dev1",
            labels={"role": "phone"},
        )

        assert device.device_id == "dev1"
        assert device.token is not None
        assert len(device.token) == 64  # hex token

        # verify persistence
        found = store.get_device("dev1")
        assert found is not None
        assert found.token == device.token

    def test_register_local_device(self, store):
        device = store.register_device(
            device_id="localhost",
            is_local=True,
        )

        assert device.is_local is True

    def test_get_device_not_found(self, store):
        assert store.get_device("nonexistent") is None

    def test_list_devices(self, store):
        store.register_device("dev1")
        store.register_device("dev2")

        devices = store.list_devices()
        assert len(devices) == 2
        assert {d.device_id for d in devices} == {"dev1", "dev2"}

    def test_touch_hello_updates_timestamp(self, store):
        device = store.register_device("dev1")
        old_hello = device.last_hello_at

        store.touch_hello("dev1")
        updated = store.get_device("dev1")

        assert updated.last_hello_at is not None

    def test_get_device_by_token(self, store):
        device = store.register_device("dev1")
        found = store.get_device_by_token(device.token)

        assert found is not None
        assert found.device_id == "dev1"

    def test_invalid_token_returns_none(self, store):
        store.register_device("dev1")
        assert store.get_device_by_token("badtoken") is None

    def test_add_capability(self, store):
        store.register_device("dev1")
        store.add_capability("dev1", {
            "name": "location",
            "display": "位置",
            "data_type": "snapshot",
            "schema": {"type": "object"},
        })
        store.approve_capability("dev1", "location")

        caps = store.get_capabilities("dev1")
        assert len(caps) == 1
        assert caps[0].name == "location"
        assert caps[0].is_approved is True

    def test_pending_registration(self, store):
        store.add_pending_registration("new-device", {"os": "Android"})

        pending = store.get_pending_registration("new-device")
        assert pending is not None
        assert pending["device_info"]["os"] == "Android"

        store.increment_retry("new-device")
        pending2 = store.get_pending_registration("new-device")
        assert pending2["retry_count"] == 1

        store.remove_pending_registration("new-device")
        assert store.get_pending_registration("new-device") is None

    def test_revoke_device(self, store):
        store.register_device("dev1")
        store.revoke_device("dev1")

        assert store.get_device("dev1") is None
        assert store.get_device_by_token("any") is None

    def test_update_labels(self, store):
        store.register_device("dev1", labels={"role": "phone"})
        store.update_labels("dev1", {"role": "tablet", "owner": "me"})

        device = store.get_device("dev1")
        assert device.labels == {"role": "tablet", "owner": "me"}
