"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from yequ.api.routes.admin import router as admin_router
from yequ.api.routes.agent import router as agent_router
from yequ.api.routes.health import router as health_router
from yequ.api.routes.maintenance import router as maintenance_router
from yequ.api.routes.yqp import router as yqp_router
from yequ.logconfig import get_logger, setup_logging

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — setup and teardown."""
    setup_logging()
    log.info("yeau center starting", version="0.1.0")

    from yequ.config import get_settings

    settings = get_settings()

    # Reject SQLite in non-test mode
    if "sqlite" in settings.database_url and not settings.test_mode:
        log.error("SQLite is not supported for production. Set YEQU_DATABASE_URL to PostgreSQL.")
        raise SystemExit("SQLite not allowed in production mode")

    # Recovery + bootstrap + background tasks: skip in test mode
    if not settings.test_mode:
        try:
            from yequ.db import async_session_factory
            from yequ.services.job_service import find_incomplete_jobs, timeout_job

            async with async_session_factory() as db:
                incomplete = await find_incomplete_jobs(db)
                recovered = 0
                for job in incomplete:
                    if (
                        job.status in ("claimed", "running")
                        and job.lease_expires_at
                        and job.lease_expires_at < datetime.now(UTC)
                    ):
                        await timeout_job(db, job, node_id="recovery")
                        recovered += 1
                if recovered:
                    await db.commit()
                    log.info("recovery complete", recovered_jobs=recovered)

        except Exception:
            log.exception("recovery scan failed")

        # Bootstrap: seed default admin token if none exist
        try:
            from sqlalchemy import select

            from yequ.db import async_session_factory
            from yequ.models.api_token import ApiToken
            from yequ.services.token_auth import hash_token

            async with async_session_factory() as db:
                result = await db.execute(
                    select(ApiToken).where(ApiToken.scope == "admin").limit(1)
                )
                if result.scalars().first() is None:
                    default_token = ApiToken(
                        token_hash=hash_token("qq756522327"),
                        scope="admin",
                        label="default admin",
                    )
                    db.add(default_token)
                    await db.commit()
                    log.info("bootstrap: default admin token created")

                # Also seed default agent token if none exist
                result2 = await db.execute(
                    select(ApiToken).where(ApiToken.scope == "agent").limit(1)
                )
                if result2.scalars().first() is None:
                    agent_token = ApiToken(
                        token_hash=hash_token("qq756522327"),
                        scope="agent",
                        label="default agent",
                    )
                    db.add(agent_token)
                    await db.commit()
                    log.info("bootstrap: default agent token created")
        except Exception:
            log.exception("token bootstrap failed")

    from yequ.services.timeline_writer import get_timeline_writer
    from yequ.services.timeout_scanner import get_scanner

    scanner = get_scanner()
    tl_writer = get_timeline_writer()

    if not settings.test_mode:
        await scanner.start()
        await tl_writer.start()

    yield

    if not settings.test_mode:
        await scanner.stop()
        await tl_writer.stop()
    log.info("yeau center shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="YeQu Center",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(agent_router)
    app.include_router(admin_router)
    app.include_router(maintenance_router)
    app.include_router(yqp_router)
    return app
