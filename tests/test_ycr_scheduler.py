import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from yequ.ycr.scheduler import (
    PRIORITY_BACKGROUND_CAPABILITY_INDEX,
    PRIORITY_FOREGROUND_CAPABILITY_EMBEDDING,
    EmbeddingScheduler,
)


@pytest.mark.asyncio
async def test_scheduler_background_waits_for_foreground() -> None:
    scheduler = EmbeddingScheduler()
    foreground_started = asyncio.Event()
    release_foreground = asyncio.Event()
    events: list[str] = []

    async def foreground_work() -> str:
        events.append("foreground-start")
        foreground_started.set()
        await release_foreground.wait()
        events.append("foreground-end")
        return "foreground"

    async def background_work() -> str:
        events.append("background-start")
        return "background"

    foreground_task = asyncio.create_task(
        scheduler.run(
            kind="embedding",
            priority=PRIORITY_FOREGROUND_CAPABILITY_EMBEDDING,
            purpose="test.foreground",
            func=foreground_work,
        )
    )
    await foreground_started.wait()
    background_task = asyncio.create_task(
        scheduler.run(
            kind="embedding",
            priority=PRIORITY_BACKGROUND_CAPABILITY_INDEX,
            purpose="test.background",
            func=background_work,
        )
    )
    await asyncio.sleep(0)
    assert events == ["foreground-start"]

    release_foreground.set()
    assert await foreground_task == "foreground"
    assert await background_task == "background"
    assert events == ["foreground-start", "foreground-end", "background-start"]
    status = scheduler.status()
    assert status["foreground"]["completed"] == 1
    assert status["background"]["completed"] == 1


@pytest.mark.asyncio
async def test_ycr_status_exposes_scheduler_metrics() -> None:
    from yequ.ycr_app import app as ycr_app

    transport = ASGITransport(app=ycr_app)
    async with AsyncClient(transport=transport, base_url="http://test-ycr") as client:
        response = await client.get("/v1/context/status")

    assert response.status_code == 200, response.text
    data = response.json()
    scheduler = data["capability_discovery"]["scheduler"]
    assert "foreground" in scheduler
    assert "background" in scheduler
    assert "inflight_dedup" in scheduler
