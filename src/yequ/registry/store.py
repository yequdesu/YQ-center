"""Device registry CRUD operations."""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

from yequ.registry.models import Device, Capability
from yequ.storage.database import get_connection
from yequ.utils import now_iso


def _generate_token() -> str:
    return secrets.token_hex(32)


class DeviceStore:
    """Manages device registration, capabilities, and pending registrations."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self):
        return get_connection(self.db_path)

    # --- Device CRUD ---

    def register_device(
        self,
        device_id: str,
        labels: dict[str, str] | None = None,
        is_local: bool = False,
    ) -> Device:
        token = _generate_token()
        now = now_iso()
        device = Device(
            device_id=device_id,
            token=token,
            labels=labels or {},
            is_local=is_local,
            created_at=now,
            updated_at=now,
        )
        row = device.to_row()

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO devices (device_id, token, labels_json, status, is_local, created_at, updated_at)
                   VALUES (:device_id, :token, :labels_json, :status, :is_local, :created_at, :updated_at)""",
                {**row, "created_at": now, "updated_at": now},
            )
            conn.commit()

        return device

    def get_device(self, device_id: str) -> Device | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ? AND status != 'revoked'",
                (device_id,),
            ).fetchone()

        if row is None:
            return None
        return Device.from_row(dict(row))

    def get_device_by_token(self, token: str) -> Device | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE token = ? AND status != 'revoked'",
                (token,),
            ).fetchone()

        if row is None:
            return None
        return Device.from_row(dict(row))

    def list_devices(self) -> list[Device]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM devices WHERE status != 'revoked' ORDER BY device_id"
            ).fetchall()

        return [Device.from_row(dict(r)) for r in rows]

    def touch_hello(self, device_id: str) -> None:
        now = now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET last_hello_at = ?, updated_at = ? WHERE device_id = ?",
                (now, now, device_id),
            )
            conn.commit()

    def revoke_device(self, device_id: str) -> None:
        now = now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET status = 'revoked', updated_at = ? WHERE device_id = ?",
                (now, device_id),
            )
            conn.commit()

    def update_labels(self, device_id: str, labels: dict[str, str]) -> None:
        now = now_iso()
        labels_json = json.dumps(labels, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "UPDATE devices SET labels_json = ?, updated_at = ? WHERE device_id = ?",
                (labels_json, now, device_id),
            )
            conn.commit()

    # --- Capability ---

    def add_capability(self, device_id: str, decl: dict[str, Any]) -> Capability:
        cap = Capability.from_declaration(device_id, decl)
        row = cap.to_row()

        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO capabilities
                   (device_id, name, display, schema_version, data_type, interval_seconds, schema_json, retention_days, is_approved)
                   VALUES (:device_id, :name, :display, :schema_version, :data_type, :interval_seconds, :schema_json, :retention_days, :is_approved)""",
                row,
            )
            conn.commit()

        return cap

    def approve_capability(self, device_id: str, name: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE capabilities SET is_approved = 1 WHERE device_id = ? AND name = ?",
                (device_id, name),
            )
            conn.commit()

    def get_capabilities(self, device_id: str) -> list[Capability]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM capabilities WHERE device_id = ? AND is_approved = 1",
                (device_id,),
            ).fetchall()

        return [Capability.from_row(dict(r)) for r in rows]

    def get_capability(self, device_id: str, name: str) -> Capability | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM capabilities WHERE device_id = ? AND name = ?",
                (device_id, name),
            ).fetchone()

        if row is None:
            return None
        return Capability.from_row(dict(row))

    # --- Pending Registrations ---

    def add_pending_registration(self, device_id: str, device_info: dict[str, Any]) -> None:
        now = now_iso()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_registrations (device_id, device_info_json, registered_at, expires_at, retry_count)
                   VALUES (?, ?, ?, datetime('now', '+1 hour'), 0)""",
                (device_id, json.dumps(device_info, ensure_ascii=False), now),
            )
            conn.commit()

    def get_pending_registration(self, device_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_registrations WHERE device_id = ? AND expires_at > datetime('now')",
                (device_id,),
            ).fetchone()

        if row is None:
            return None
        d = dict(row)
        d["device_info"] = json.loads(d.pop("device_info_json"))
        return d

    def increment_retry(self, device_id: str) -> int:
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_registrations SET retry_count = retry_count + 1 WHERE device_id = ?",
                (device_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT retry_count FROM pending_registrations WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            return row["retry_count"] if row else 0

    def remove_pending_registration(self, device_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM pending_registrations WHERE device_id = ?",
                (device_id,),
            )
            conn.commit()
