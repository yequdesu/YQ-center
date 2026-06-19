"""Token scope authentication for Admin and Agent tokens."""

import hashlib

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.api_token import ApiToken
from yequ.models.timeline import TimelineEvent


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _write_audit(
    db: AsyncSession,
    event_type: str,
    *,
    scope: str | None = None,
    detail: str = "",
) -> None:
    """Write an auth audit event."""
    # Compute next global_seq
    from sqlalchemy import func, select
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type=event_type,
        actor_type="system",
        actor_id="token_auth",
        data={
            "scope": scope,
            "detail": detail,
        },
    )
    db.add(event)
    await db.flush()


async def authenticate_scoped_token(
    db: AsyncSession,
    authorization: str | None,
    required_scope: str,
) -> dict[str, str]:
    """Authenticate a token with a required scope.

    Args:
        db: AsyncSession
        authorization: Raw Authorization header value
        required_scope: "admin" or "agent" or "node"

    Returns:
        {"scope": str, "label": str} on success

    Raises:
        HTTPException(401): Missing or invalid token
        HTTPException(403): Wrong scope for this endpoint
    """
    if not authorization:
        await _write_audit(db, "auth.failed", scope=required_scope, detail="missing header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        await _write_audit(db, "auth.failed", scope=required_scope, detail="bad format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )

    token = parts[1]
    if not token or len(token) < 8:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    computed_hash = hash_token(token)

    # Look up in ApiToken table
    result = await db.execute(
        select(ApiToken).where(ApiToken.token_hash == computed_hash)
    )
    api_token = result.scalar_one_or_none()

    if api_token is None:
        await _write_audit(db, "auth.failed", scope=required_scope, detail="invalid token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Check scope
    if api_token.scope != required_scope:
        await _write_audit(
            db, "token.scope_denied",
            scope=api_token.scope,
            detail=f"token scope={api_token.scope}, required={required_scope}",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token scope '{api_token.scope}' cannot access {required_scope} endpoints",
        )

    return {"scope": api_token.scope, "label": api_token.label}
