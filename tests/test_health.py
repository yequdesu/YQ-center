"""Tests for the /healthz endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok(client: AsyncClient):
    """Health check should return status ok when DB is connected."""
    response = await client.get("/healthz")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["database"] == "connected"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_healthz_response_is_json(client: AsyncClient):
    """Health check should return JSON content type."""
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
