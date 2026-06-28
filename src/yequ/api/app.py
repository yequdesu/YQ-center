"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from yequ.api.routes.admin import router as admin_router
from yequ.api.routes.admin_activity import router as admin_activity_router
from yequ.api.routes.admin_approvals import router as admin_approvals_router
from yequ.api.routes.admin_artifacts import router as admin_artifacts_router
from yequ.api.routes.admin_invocations import router as admin_invocations_router
from yequ.api.routes.admin_nodes import router as admin_nodes_router
from yequ.api.routes.admin_provisioning import router as admin_provisioning_router
from yequ.api.routes.admin_sessions import router as admin_sessions_router
from yequ.api.routes.agent import router as agent_router
from yequ.api.routes.health import router as health_router
from yequ.api.routes.maintenance import router as maintenance_router
from yequ.api.routes.yqp import router as yqp_router
from yequ.config import PROJECT_ROOT
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

    from yequ.services.approval_service import _scan_expired_approvals
    from yequ.services.message_dedup import get_yqp_message_cleanup_scanner
    from yequ.services.node_liveness_scanner import get_liveness_scanner
    from yequ.services.signal_state_scanner import get_signal_state_scanner
    from yequ.services.timeline_writer import get_timeline_writer
    from yequ.services.timeout_scanner import get_scanner

    scanner = get_scanner()
    tl_writer = get_timeline_writer()
    liveness_scanner = get_liveness_scanner()
    signal_state_scanner = get_signal_state_scanner()
    yqp_message_cleanup_scanner = get_yqp_message_cleanup_scanner()

    if not settings.test_mode:
        await scanner.start()
        await tl_writer.start()
        await liveness_scanner.start()
        await signal_state_scanner.start()
        await yqp_message_cleanup_scanner.start()
        approval_scanner_task = asyncio.create_task(
            _scan_expired_approvals(), name="approval-expiry-scanner"
        )

    yield

    if not settings.test_mode:
        approval_scanner_task.cancel()
        with suppress(asyncio.CancelledError):
            await approval_scanner_task
        await yqp_message_cleanup_scanner.stop()
        await signal_state_scanner.stop()
        await liveness_scanner.stop()
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

    # ── Debug: log raw request body for encoding diagnostics ──
    @app.middleware("http")
    async def log_raw_body(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        from yequ.config import get_settings

        settings = get_settings()
        if (settings.debug or settings.debug_timeline) and request.method in (
            "POST",
            "PUT",
            "PATCH",
        ):
            body_bytes = await request.body()
            body_str = body_bytes.decode("utf-8", errors="replace")
            has_utf8 = any(ord(c) > 127 for c in body_str)
            has_replacement = "�" in body_str
            log.info(
                "raw body: method=%s path=%s size=%d has_utf8=%s has_replacement=%s preview=%s",
                request.method,
                request.url.path,
                len(body_bytes),
                has_utf8,
                has_replacement,
                repr(body_str[:200]),
            )
            # Re-attach body so route handlers can read it
            request._body = body_bytes
        response = await call_next(request)
        return response

    app.include_router(health_router)
    app.include_router(agent_router)
    app.include_router(admin_router)
    app.include_router(admin_activity_router)
    app.include_router(admin_approvals_router)
    app.include_router(admin_artifacts_router)
    app.include_router(admin_invocations_router)
    app.include_router(admin_nodes_router)
    app.include_router(admin_provisioning_router)
    app.include_router(admin_sessions_router)
    app.include_router(maintenance_router)
    app.include_router(yqp_router)

    # ── Console SPA static files ──
    _console_dir = PROJECT_ROOT / "console-dist"
    if _console_dir.exists() and (_console_dir / "index.html").exists():
        # Mount static assets (JS, CSS, favicon, etc.)
        _assets_dir = _console_dir / "assets"
        if _assets_dir.exists():
            app.mount(
                "/console/assets",
                StaticFiles(directory=str(_assets_dir)),
                name="console_assets",
            )

        # Favicon
        _favicon = _console_dir / "favicon.svg"
        if _favicon.exists():

            @app.get("/console/favicon.svg", include_in_schema=False)
            async def _console_favicon() -> FileResponse:
                return FileResponse(_favicon)

        # SPA fallback: all /console/* routes serve index.html for client-side routing
        @app.get("/console/{full_path:path}", include_in_schema=False)
        async def _console_spa(full_path: str) -> FileResponse:
            # Actual files at root level (favicon, etc.) are handled by explicit routes above
            return FileResponse(_console_dir / "index.html")

        @app.get("/console", include_in_schema=False)
        async def _console_index() -> FileResponse:
            return FileResponse(_console_dir / "index.html")

    return app
