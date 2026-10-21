"""Main FastAPI application entry point with lifespan database management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routes import router as telemetry_router
from src.core.config import settings
from src.core.database import close_database_connections, connect_to_databases


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown lifecycle events for database pools."""
    await connect_to_databases()
    try:
        yield
    finally:
        await close_database_connections()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(telemetry_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok", "environment": settings.ENVIRONMENT}
