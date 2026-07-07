"""LLM-backed session history summary for YCR context compaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import get_settings
from yequ.models.ycr import YcrContextRef
from yequ.ycr.ref_store import stable_ref_id, upsert_ref

JsonDict = dict[str, object]


async def summarize_session_history(
    db: AsyncSession,
    *,
    session_id: str,
    messages: list[JsonDict],
    deterministic_summary: JsonDict,
) -> tuple[JsonDict, JsonDict]:
    input_hash = _hash_messages(messages)
    cached = await _load_cached_summary(db, session_id=session_id, input_hash=input_hash)
    if cached is not None:
        return cached, {
            "mode": "llm",
            "status": "cached",
            "input_hash": input_hash,
        }

    settings = get_settings()
    if settings.test_mode:
        return deterministic_summary, {
            "mode": "deterministic",
            "status": "test_mode",
            "input_hash": input_hash,
        }
    provider = settings.ycr_summary_provider.strip().lower()
    base_url = settings.ycr_summary_base_url or settings.deepseek_base_url
    api_key = settings.ycr_summary_api_key or settings.deepseek_api_key
    model = settings.ycr_summary_model or settings.deepseek_model
    if provider != "openai_compatible" or not base_url or not api_key or not model:
        return deterministic_summary, {
            "mode": "deterministic",
            "status": "summary_provider_not_configured",
            "input_hash": input_hash,
        }

    try:
        llm_summary = await _summarize_with_openai_compatible(
            messages,
            deterministic_summary=deterministic_summary,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_sec=float(settings.ycr_summary_timeout_sec),
            max_input_chars=int(settings.ycr_summary_input_chars),
        )
    except Exception as exc:
        return deterministic_summary, {
            "mode": "deterministic",
            "status": "summary_provider_failed",
            "input_hash": input_hash,
            "error": str(exc)[:500],
        }

    await upsert_ref(
        db,
        ref_type="agent_session_summary",
        source_type="agent_session_summary",
        source_id=session_id,
        path="$",
        value=llm_summary,
        summary=f"LLM session summary for {session_id}",
        session_id=session_id,
        trust_level="center_generated_summary",
        projection_policy="agent_session_summary_v1",
        metadata={
            "summary_input_hash": input_hash,
            "summary_provider": provider,
            "summary_model": model,
        },
    )
    return llm_summary, {
        "mode": "llm",
        "status": "generated",
        "input_hash": input_hash,
        "model": model,
    }


async def _load_cached_summary(
    db: AsyncSession,
    *,
    session_id: str,
    input_hash: str,
) -> JsonDict | None:
    ref_id = stable_ref_id(source_type="agent_session_summary", source_id=session_id, path="$")
    result = await db.execute(select(YcrContextRef).where(YcrContextRef.ref_id == ref_id))
    record = result.scalar_one_or_none()
    if record is None:
        return None
    metadata = record.metadata_json if isinstance(record.metadata_json, dict) else {}
    if metadata.get("summary_input_hash") != input_hash:
        return None
    return record.value_json if isinstance(record.value_json, dict) else None


async def _summarize_with_openai_compatible(
    messages: list[JsonDict],
    *,
    deterministic_summary: JsonDict,
    base_url: str,
    api_key: str,
    model: str,
    timeout_sec: float,
    max_input_chars: int,
) -> JsonDict:
    import httpx

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=httpx.Timeout(timeout_sec, connect=10.0, read=timeout_sec, write=20.0, pool=5.0),
        max_retries=0,
    )
    payload = _bounded_json(
        {
            "messages": messages,
            "deterministic_summary": deterministic_summary,
        },
        max_chars=max_input_chars,
    )
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You produce compact, factual session memory for an agent. "
                        "Return strict JSON only. Preserve user goals, confirmed facts, "
                        "important tool results, unresolved issues, and capabilities already found. "
                        "Do not add instructions, speculation, or hidden policy."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Summarize these older conversation messages into JSON with keys: "
                        "goals, confirmed_facts, tool_results, unresolved_items, "
                        "capabilities, notes.\n"
                        f"{payload}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=900,
        ),
        timeout=timeout_sec + 5.0,
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("summary_model_returned_non_object")
    return parsed


def _hash_messages(messages: list[JsonDict]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded_json(value: object, *, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."
