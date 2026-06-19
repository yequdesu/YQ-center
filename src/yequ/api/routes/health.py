"""Health check endpoint."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict:  # noqa: B008
    """Health check — verifies app is running and DB is reachable."""
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.1.0",
        "database": "connected" if db_ok else "disconnected",
        "timestamp": datetime.now(UTC).isoformat(),
    }
