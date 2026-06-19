"""Node authentication — token verification and node identity binding."""

import hashlib

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.node import Node


def hash_token(token: str) -> str:
    """Hash a token using SHA-256.

    Tokens are stored as hashes, never in plaintext.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def authenticate_node(
    db: AsyncSession,
    authorization: str | None,
) -> Node:
    """Authenticate a Node from Bearer token in Authorization header.

    Looks up the Node by hashed token. Raises 401 on failure.
    Returns the authenticated Node ORM object.

    Args:
        db: Async database session
        authorization: Raw Authorization header value (e.g., "Bearer <token>")

    Returns:
        The authenticated Node

    Raises:
        HTTPException(401): Missing/invalid Authorization header or token
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Parse "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
        )

    token = parts[1]
    if not token or len(token) < 8:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
        )

    computed_hash = hash_token(token)

    result = await db.execute(
        select(Node).where(Node.token_hash == computed_hash)
    )
    node = result.scalar_one_or_none()

    if node is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return node


async def verify_node_id_binding(node: Node, envelope_node_id: str) -> None:
    """Verify that the envelope node_id matches the authenticated Node.

    This MUST be called after parsing the YQP envelope to ensure
    the node_id in the message matches the token's bound node.

    Raises:
        HTTPException(403): node_id does not match token binding
    """
    if node.node_id != envelope_node_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"node_id {envelope_node_id!r} does not match token binding",
        )
