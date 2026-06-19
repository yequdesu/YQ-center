"""Tests for token scope authentication hardening."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_endpoints_reject_no_auth_when_enabled(
    client: AsyncClient, monkeypatch
):
    """When require_admin_auth=True, admin endpoints should reject unauthenticated requests."""
    from yequ.config import Settings

    settings = Settings(require_admin_auth=True)
    monkeypatch.setattr("yequ.config._settings", settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: settings)

    r = await client.get("/admin/nodes")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    r = await client.get("/admin/jobs")
    assert r.status_code == 401

    r = await client.get("/admin/timeline")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_agent_endpoints_reject_no_auth_when_enabled(
    client: AsyncClient, monkeypatch
):
    """When require_admin_auth=True, agent endpoints should reject unauthenticated requests."""
    from yequ.config import Settings

    settings = Settings(require_admin_auth=True)
    monkeypatch.setattr("yequ.config._settings", settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: settings)

    r = await client.post("/agent/sessions", json={"actor_id": "test"})
    assert r.status_code == 401

    r = await client.post("/agent/invoke", json={
        "session_id": "sess_test", "prompt": "test",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_token_allows_access(client: AsyncClient, monkeypatch):
    """A valid admin-scoped token should allow access to admin endpoints."""
    from yequ.config import Settings

    settings = Settings(require_admin_auth=True)
    monkeypatch.setattr("yequ.config._settings", settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: settings)

    from yequ.db import async_session_factory
    from yequ.models.api_token import ApiToken
    from yequ.services.token_auth import hash_token

    # Create an admin token in the DB
    async with async_session_factory() as db:
        t = ApiToken(
            token_hash=hash_token("admin-secret-token"),
            scope="admin",
            label="test admin",
        )
        db.add(t)
        await db.commit()

    # Should work with valid token
    auth = {"Authorization": "Bearer admin-secret-token"}
    r = await client.get("/admin/nodes", headers=auth)
    assert r.status_code == 200

    # Clean up the test token
    async with async_session_factory() as db:
        result = await db.execute(
            __import__("sqlalchemy").select(ApiToken).where(
                ApiToken.label == "test admin"
            )
        )
        t = result.scalar_one_or_none()
        if t:
            await db.delete(t)
            await db.commit()


@pytest.mark.asyncio
async def test_admin_token_rejected_on_agent_endpoint(
    client: AsyncClient, monkeypatch
):
    """Admin token should be rejected on agent endpoints (scope mismatch = 403)."""
    from yequ.config import Settings

    settings = Settings(require_admin_auth=True)
    monkeypatch.setattr("yequ.config._settings", settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: settings)

    from yequ.db import async_session_factory
    from yequ.models.api_token import ApiToken
    from yequ.services.token_auth import hash_token

    async with async_session_factory() as db:
        t = ApiToken(
            token_hash=hash_token("admin-token-2"),
            scope="admin",
            label="test admin-scope-rejection",
        )
        db.add(t)
        await db.commit()

    auth = {"Authorization": "Bearer admin-token-2"}
    r = await client.post("/agent/sessions", json={"actor_id": "test"}, headers=auth)
    assert r.status_code == 403  # wrong scope

    # Clean up
    async with async_session_factory() as db:
        result = await db.execute(
            __import__("sqlalchemy").select(ApiToken).where(
                ApiToken.label == "test admin-scope-rejection"
            )
        )
        t = result.scalar_one_or_none()
        if t:
            await db.delete(t)
            await db.commit()


@pytest.mark.asyncio
async def test_create_token_via_admin_api(client: AsyncClient, monkeypatch):
    """POST /admin/tokens should create a new API token."""
    from yequ.config import Settings

    settings = Settings(require_admin_auth=True)
    monkeypatch.setattr("yequ.config._settings", settings)
    monkeypatch.setattr("yequ.api.deps._get_settings", lambda: settings)

    from yequ.db import async_session_factory
    from yequ.models.api_token import ApiToken
    from yequ.services.token_auth import hash_token

    # First create an admin token
    async with async_session_factory() as db:
        t = ApiToken(
            token_hash=hash_token("super-admin-token"),
            scope="admin",
            label="super admin",
        )
        db.add(t)
        await db.commit()

    auth = {"Authorization": "Bearer super-admin-token"}

    # Create a new agent token via admin API
    r = await client.post(
        "/admin/tokens",
        json={
            "token": "new-agent-token-123",
            "scope": "agent",
            "label": "test agent token",
        },
        headers=auth,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["scope"] == "agent"
    assert data["label"] == "test agent token"

    # Clean up
    async with async_session_factory() as db:
        result = await db.execute(
            __import__("sqlalchemy").select(ApiToken).where(
                ApiToken.label.in_(["super admin", "test agent token"])
            )
        )
        for t in result.scalars().all():
            await db.delete(t)
        await db.commit()
