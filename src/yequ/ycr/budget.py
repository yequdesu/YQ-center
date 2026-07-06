"""YCR projection profiles and lightweight token estimates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from yequ.config import Settings


@dataclass(frozen=True, slots=True)
class ProjectionLimit:
    inline_bytes: int
    preview_chars: int


@dataclass(frozen=True, slots=True)
class ProjectionProfile:
    provider: str
    model: str
    default: ProjectionLimit
    source_limits: dict[str, ProjectionLimit]
    min_ref_savings_ratio: float = 0.25

    def limit_for(self, source_kind: str) -> ProjectionLimit:
        return self.source_limits.get(source_kind, self.default)


def projection_profile_from_settings(settings: Settings) -> ProjectionProfile:
    default_inline = max(256, int(settings.ycr_projection_inline_bytes))
    default_preview = max(0, int(settings.ycr_projection_preview_chars))
    return ProjectionProfile(
        provider="deepseek",
        model=settings.deepseek_model,
        default=ProjectionLimit(
            inline_bytes=default_inline,
            preview_chars=default_preview,
        ),
        source_limits={
            "tool_result": ProjectionLimit(
                inline_bytes=max(256, int(settings.ycr_tool_result_inline_bytes)),
                preview_chars=max(0, int(settings.ycr_tool_result_preview_chars)),
            ),
            "operation_observation": ProjectionLimit(
                inline_bytes=max(default_inline, int(settings.ycr_context_block_inline_bytes)),
                preview_chars=max(default_preview, int(settings.ycr_context_block_preview_chars)),
            ),
            "context_blocks": ProjectionLimit(
                inline_bytes=max(default_inline, int(settings.ycr_context_block_inline_bytes)),
                preview_chars=max(default_preview, int(settings.ycr_context_block_preview_chars)),
            ),
        },
    )


def estimate_tokens(value: object, *, model: str | None = None) -> int:
    text = _token_text(value)
    counter = _token_counter(model)
    if counter is not None:
        return max(1, len(counter.encode(text)))
    return max(1, len(text.encode()) // 4)


def token_accounting_metadata(*, model: str | None = None) -> dict[str, object]:
    return {
        "method": "tiktoken" if _token_counter(model) is not None else "byte_div_4",
        "model": model or "",
        "precise": _token_counter(model) is not None,
    }


def json_size_bytes(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode())
    except TypeError:
        return len(str(value).encode())


def _token_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


@lru_cache(maxsize=16)
def _token_counter(model: str | None) -> Any | None:
    try:
        import tiktoken  # type: ignore[import-not-found]
    except Exception:
        return None
    model_name = (model or "").strip()
    try:
        if model_name:
            return tiktoken.encoding_for_model(model_name)
    except KeyError:
        pass
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None
