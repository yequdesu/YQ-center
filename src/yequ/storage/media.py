"""Media storage — screenshots and other binary command results."""

from __future__ import annotations

import base64
import os
import uuid

from yequ.utils import now_iso


def save_media(data_dir: str, device_id: str, filename: str,
               data_base64: str, mime_type: str = "image/png") -> str | None:
    """Save base64-encoded media to disk. Returns the media ID (filename stem)."""
    try:
        raw = base64.b64decode(data_base64)
    except Exception:
        return None

    media_dir = os.path.join(data_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    ext = mime_type.split("/")[-1] if "/" in mime_type else "png"
    media_id = f"{device_id}_{now_iso().replace(':', '-')}_{uuid.uuid4().hex[:8]}"
    fname = f"{media_id}.{ext}"

    with open(os.path.join(media_dir, fname), "wb") as f:
        f.write(raw)

    # Write metadata alongside
    meta_path = os.path.join(media_dir, f"{media_id}.json")
    import json
    with open(meta_path, "w") as f:
        json.dump({
            "device_id": device_id,
            "filename": fname,
            "mime_type": mime_type,
            "created_at": now_iso(),
        }, f)

    return media_id


def get_media_path(data_dir: str, media_id: str) -> str | None:
    """Find the media file path by ID. Returns None if not found."""
    media_dir = os.path.join(data_dir, "media")
    if not os.path.isdir(media_dir):
        return None
    for f in os.listdir(media_dir):
        if f.startswith(media_id) and not f.endswith(".json"):
            return os.path.join(media_dir, f)
    return None
