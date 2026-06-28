"""FastAPI dependency injection."""

from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yequ import db as yequ_db
from yequ.config import Settings
from yequ.config import get_settings as _get_settings
from yequ.db import get_db as _get_db
from yequ.models.node import Node
from yequ.services.node_auth import authenticate_node
from yequ.services.token_auth import authenticate_scoped_token


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session."""
    async for session in _get_db():
        yield session


def get_settings() -> Settings:
    """Dependency: provides application settings."""
    return _get_settings()


async def get_current_node(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> Node:
    """Dependency: authenticate Node from Bearer token.

    Extracts the Authorization header, hashes the token, looks up
    the Node in the database. Returns the authenticated Node.
    Raises 401 on any auth failure.
    """
    return await authenticate_node(db, authorization)


async def get_admin_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, str]:
    """Dependency: requires admin-scoped token.

    If require_admin_auth is False (default/test mode), returns a
    dummy token payload so existing tests continue to work.
    """
    if not get_settings().require_admin_auth:
        return {"scope": "admin", "label": "test"}
    return await _authenticate_scoped_token_short_session(authorization, "admin")


async def get_agent_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, str]:
    """Dependency: requires agent-scoped token.

    If require_admin_auth is False (default/test mode), returns a
    dummy token payload so existing tests continue to work.
    """
    if not get_settings().require_admin_auth:
        return {"scope": "agent", "label": "test"}
    return await _authenticate_scoped_token_short_session(authorization, "agent")


async def _authenticate_scoped_token_short_session(
    authorization: str | None,
    required_scope: str,
) -> dict[str, str]:
    async with yequ_db.async_session_factory() as db:
        try:
            token = await authenticate_scoped_token(db, authorization, required_scope)
        except HTTPException:
            await db.commit()
            raise
        except Exception:
            await db.rollback()
            raise
        await db.rollback()
        return token
