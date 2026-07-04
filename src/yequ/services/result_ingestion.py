"""Result ingestion guards for Node-reported job outputs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_RESULT_JSON_BYTES = 256 * 1024
MAX_STRING_BYTES = 64 * 1024
MAX_LIST_ITEMS = 2000


def guard_job_output(value: object, *, job_id: str) -> object:
    """Keep job output bounded before it is persisted or copied to Invocation."""
    if value is None:
        return None
    if _json_size(value) <= MAX_RESULT_JSON_BYTES:
        return _guard_node(value, job_id=job_id, path="$")
    return {
        "ycr_ingestion": {
            "truncated": True,
            "reason": "job_output_json_too_large",
            "job_id": job_id,
            "raw_size_bytes": _json_size(value),
            "source_hash": _digest(value),
        },
        "summary": _shape_summary(value),
        "refs": [
            {
                "ref_id": _ref_id(job_id=job_id, path="$"),
                "ref_type": "job_output",
                "source_anchor": {"type": "job", "id": job_id},
                "path": "$",
                "summary": "Raw job output exceeded Center ingestion budget.",
            }
        ],
    }


def _guard_node(value: object, *, job_id: str, path: str) -> object:
    if isinstance(value, dict):
        return {
            str(key): _guard_node(item, job_id=job_id, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        if len(value) <= MAX_LIST_ITEMS:
            return [
                _guard_node(item, job_id=job_id, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        return {
            "ycr_ingestion": {
                "truncated": True,
                "reason": "list_item_limit",
                "job_id": job_id,
                "path": path,
                "count": len(value),
            },
            "sample": [
                _guard_node(item, job_id=job_id, path=f"{path}[{index}]")
                for index, item in enumerate(value[:20])
            ],
        }
    if isinstance(value, str):
        encoded = value.encode()
        if len(encoded) <= MAX_STRING_BYTES:
            return value
        return {
            "ycr_ingestion": {
                "truncated": True,
                "reason": "string_size_limit",
                "job_id": job_id,
                "path": path,
                "raw_size_bytes": len(encoded),
                "source_hash": _digest(value),
            },
            "preview": value[:4096],
            "refs": [
                {
                    "ref_id": _ref_id(job_id=job_id, path=path),
                    "ref_type": "job_output",
                    "source_anchor": {"type": "job", "id": job_id},
                    "path": path,
                    "summary": "Raw string field exceeded Center ingestion budget.",
                }
            ],
        }
    return value


def ingestion_refs(value: object, *, job_id: str) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    _collect_refs(value, job_id=job_id, path="$", refs=refs)
    return refs


def select_path(value: object, path: str) -> object:
    if path in {"", "$"}:
        return value
    current: Any = value
    parts = path[2:].split(".") if path.startswith("$.") else path.split(".")
    for part in parts:
        if not part:
            continue
        if "[" in part and part.endswith("]"):
            key, raw_index = part[:-1].split("[", 1)
            if key:
                if not isinstance(current, dict):
                    raise ValueError(f"Cannot select {path}: {key} is not an object")
                current = current[key]
            if not isinstance(current, list):
                raise ValueError(f"Cannot select {path}: {part} is not a list")
            current = current[int(raw_index)]
            continue
        if not isinstance(current, dict):
            raise ValueError(f"Cannot select {path}: {part} is not an object")
        current = current[part]
    return current


def _json_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode())
    except TypeError:
        return len(str(value).encode())


def _digest(value: object) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        payload = str(value)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _ref_id(*, job_id: str, path: str) -> str:
    return f"ctxref_{hashlib.sha256(f'job:{job_id}:{path}'.encode()).hexdigest()[:16]}"


def _collect_refs(
    value: object,
    *,
    job_id: str,
    path: str,
    refs: list[dict[str, object]],
) -> None:
    if isinstance(value, dict):
        meta = value.get("ycr_ingestion")
        if isinstance(meta, dict) and meta.get("truncated") is True:
            refs.append(
                {
                    "ref_id": _ref_id(job_id=job_id, path=str(meta.get("path") or path)),
                    "ref_type": "job_output",
                    "source_anchor": {"type": "job", "id": job_id},
                    "path": str(meta.get("path") or path),
                    "summary": "Raw job output field was persisted as YCR context.",
                }
            )
        for key, item in value.items():
            _collect_refs(item, job_id=job_id, path=f"{path}.{key}", refs=refs)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_refs(item, job_id=job_id, path=f"{path}[{index}]", refs=refs)


def _shape_summary(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {"type": "object", "key_count": len(value), "keys": list(value.keys())[:20]}
    if isinstance(value, list):
        return {"type": "array", "count": len(value)}
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    return {"type": type(value).__name__}
