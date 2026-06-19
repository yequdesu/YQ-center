"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from yequ.api.routes.health import router as health_router
from yequ.logconfig import get_logger, setup_logging

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — setup and teardown."""
    setup_logging()
    log.info("yeau center starting", version="0.1.0")
    yield
    log.info("yeau center shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="YeQu Center",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    return app
