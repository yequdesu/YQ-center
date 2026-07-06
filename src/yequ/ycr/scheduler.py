"""YCR model request scheduler.

The scheduler protects local embedding/rerank runtimes from background index
work while keeping foreground Agent searches responsive.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from yequ.config import get_settings
from yequ.ycr import embedding as embedding_module
from yequ.ycr.embedding import RerankItem, YcrEmbedding

T = TypeVar("T")

PRIORITY_FOREGROUND_CAPABILITY_EMBEDDING = 0
PRIORITY_FOREGROUND_CAPABILITY_RERANK = 1
PRIORITY_FOREGROUND_CONTEXT_EMBEDDING = 2
PRIORITY_BACKGROUND_CAPABILITY_INDEX = 5
PRIORITY_BACKGROUND_CONTEXT_INDEX = 6
BACKGROUND_PRIORITY_MIN = 5


@dataclass(frozen=True)
class SchedulerToken:
    kind: str
    priority: int
    purpose: str
    foreground: bool


class EmbeddingScheduler:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._embedding_sem: asyncio.Semaphore | None = None
        self._rerank_sem: asyncio.Semaphore | None = None
        self._foreground_waiting = 0
        self._foreground_active = 0
        self._background_waiting = 0
        self._background_active = 0
        self._completed_foreground = 0
        self._completed_background = 0
        self._last_foreground_wait_ms = 0.0
        self._last_background_wait_ms = 0.0
        self._last_foreground_duration_ms = 0.0
        self._last_background_duration_ms = 0.0
        self._inflight: dict[str, asyncio.Task[object]] = {}

    async def run(
        self,
        *,
        kind: str,
        priority: int,
        purpose: str,
        func: Callable[[], Awaitable[T]],
        cache_key: str | None = None,
        deadline_sec: float | None = None,
    ) -> T:
        if cache_key:
            inflight_key = f"{kind}:{cache_key}"
            existing: asyncio.Task[object] | None = None
            task: asyncio.Task[T] | None = None
            async with self._condition:
                existing = self._inflight.get(inflight_key)
                if existing is None:
                    task = asyncio.create_task(
                        self._run_uncached(
                            kind=kind,
                            priority=priority,
                            purpose=purpose,
                            func=func,
                            deadline_sec=deadline_sec,
                        )
                    )
                    self._inflight[inflight_key] = task
            if existing is not None:
                return await asyncio.shield(existing)  # type: ignore[return-value]
            assert task is not None
            try:
                return await asyncio.shield(task)
            finally:
                async with self._condition:
                    if self._inflight.get(inflight_key) is task:
                        self._inflight.pop(inflight_key, None)

        return await self._run_uncached(
            kind=kind,
            priority=priority,
            purpose=purpose,
            func=func,
            deadline_sec=deadline_sec,
        )

    async def _run_uncached(
        self,
        *,
        kind: str,
        priority: int,
        purpose: str,
        func: Callable[[], Awaitable[T]],
        deadline_sec: float | None,
    ) -> T:
        token = await self._enter(kind=kind, priority=priority, purpose=purpose)
        start = time.perf_counter()
        semaphore = self._semaphore(kind)
        try:
            async with semaphore:
                if deadline_sec is None:
                    return await func()
                return await asyncio.wait_for(func(), timeout=deadline_sec)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            await self._leave(token, duration_ms=duration_ms)

    async def _enter(self, *, kind: str, priority: int, purpose: str) -> SchedulerToken:
        foreground = priority < BACKGROUND_PRIORITY_MIN
        wait_started = time.perf_counter()
        async with self._condition:
            if foreground:
                self._foreground_waiting += 1
                self._condition.notify_all()
                self._foreground_waiting -= 1
                self._foreground_active += 1
                self._last_foreground_wait_ms = (time.perf_counter() - wait_started) * 1000
                return SchedulerToken(kind, priority, purpose, foreground=True)

            self._background_waiting += 1
            try:
                while self._foreground_waiting > 0 or self._foreground_active > 0:
                    await self._condition.wait()
                self._background_waiting -= 1
                self._background_active += 1
                self._last_background_wait_ms = (time.perf_counter() - wait_started) * 1000
                return SchedulerToken(kind, priority, purpose, foreground=False)
            except Exception:
                self._background_waiting -= 1
                self._condition.notify_all()
                raise

    async def _leave(self, token: SchedulerToken, *, duration_ms: float) -> None:
        async with self._condition:
            if token.foreground:
                self._foreground_active = max(0, self._foreground_active - 1)
                self._completed_foreground += 1
                self._last_foreground_duration_ms = duration_ms
            else:
                self._background_active = max(0, self._background_active - 1)
                self._completed_background += 1
                self._last_background_duration_ms = duration_ms
            self._condition.notify_all()

    def _semaphore(self, kind: str) -> asyncio.Semaphore:
        settings = get_settings()
        if kind == "rerank":
            if self._rerank_sem is None:
                self._rerank_sem = asyncio.Semaphore(
                    max(1, settings.ycr_scheduler_rerank_concurrency)
                )
            return self._rerank_sem
        if self._embedding_sem is None:
            self._embedding_sem = asyncio.Semaphore(
                max(1, settings.ycr_scheduler_embedding_concurrency)
            )
        return self._embedding_sem

    def status(self) -> dict[str, object]:
        return {
            "foreground": {
                "waiting": self._foreground_waiting,
                "active": self._foreground_active,
                "completed": self._completed_foreground,
                "last_wait_ms": round(self._last_foreground_wait_ms, 2),
                "last_duration_ms": round(self._last_foreground_duration_ms, 2),
            },
            "background": {
                "waiting": self._background_waiting,
                "active": self._background_active,
                "completed": self._completed_background,
                "last_wait_ms": round(self._last_background_wait_ms, 2),
                "last_duration_ms": round(self._last_background_duration_ms, 2),
            },
            "inflight_dedup": len(self._inflight),
        }


_scheduler = EmbeddingScheduler()


def scheduler_status() -> dict[str, object]:
    return _scheduler.status()


async def scheduled_embed_text_full(
    text: str,
    *,
    priority: int,
    purpose: str,
    cache_key: str | None = None,
    deadline_sec: float | None = None,
) -> YcrEmbedding:
    return await _scheduler.run(
        kind="embedding",
        priority=priority,
        purpose=purpose,
        cache_key=cache_key,
        deadline_sec=deadline_sec,
        func=lambda: embedding_module.embed_text_full(text),
    )


async def scheduled_embed_text(
    text: str,
    *,
    priority: int,
    purpose: str,
    cache_key: str | None = None,
    deadline_sec: float | None = None,
) -> tuple[list[float], str, str]:
    embedding = await scheduled_embed_text_full(
        text,
        priority=priority,
        purpose=purpose,
        cache_key=cache_key,
        deadline_sec=deadline_sec,
    )
    return embedding.dense, embedding.provider, embedding.model


async def scheduled_rerank_documents(
    query: str,
    documents: list[str],
    *,
    priority: int,
    purpose: str,
    top_n: int,
    cache_key: str | None = None,
    deadline_sec: float | None = None,
) -> list[RerankItem]:
    return await _scheduler.run(
        kind="rerank",
        priority=priority,
        purpose=purpose,
        cache_key=cache_key,
        deadline_sec=deadline_sec,
        func=lambda: embedding_module.rerank_documents(query, documents, top_n=top_n),
    )
