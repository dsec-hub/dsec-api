"""FastAPI app factory — the extensible base every feature mounts onto.

Adding a new integration is a folder under `features/` exposing an `APIRouter`
plus a single `include_router` line below. Nothing else changes. The email
feature is just the first plugin mounted on this base.

Exposes a module-level `app` named so Vercel auto-detects it (entrypoint
`app/main.py`). Runs locally with `uvicorn app.main:app --reload`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import require_basic_auth
from app.config import settings
from app.db import run_migrations

# Feature routers — each self-contained under features/<name>/router.py
from app.dashboard.router import router as dashboard_router
from app.features.admin.router import router as admin_router
from app.features.calcom.router import router as calcom_router
from app.features.discord.router import router as discord_router
from app.features.email.router import router as email_router
from app.features.events.router import router as events_router
from app.features.notion.router import router as notion_router
from app.features.public.router import router as public_router

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("dsec")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: bring the schema to head (skippable in serverless — see
    # RUN_MIGRATIONS_ON_STARTUP) and log readiness.
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_migrations()
    _logger.info("DSEC agent API started (db=%s)", settings.DATABASE_URL.split("@")[-1])
    yield
    # Shutdown: nothing to tear down (no persistent in-process state).


def create_app() -> FastAPI:
    """Build and configure the FastAPI instance."""
    app = FastAPI(
        title="DSEC Agent API",
        version="1.0.0",
        description="Extensible integration server. v1 ships the email agent.",
        lifespan=lifespan,
        # Keep the API surface private: docs are gated behind basic auth below.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    _register_exception_handlers(app)
    _register_gated_docs(app)

    # --- Mount features. One line each; order is cosmetic. ---
    app.include_router(email_router, prefix="/email", tags=["email"])
    app.include_router(public_router, prefix="/public", tags=["public"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    app.include_router(events_router, prefix="/admin", tags=["admin", "events"])
    app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
    app.include_router(discord_router, prefix="/discord", tags=["discord"])
    app.include_router(calcom_router, prefix="/calcom", tags=["calcom"])
    app.include_router(notion_router, prefix="/notion", tags=["notion"])

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Centralised handling so no feature leaks a stack trace to the caller."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "invalid request", "errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        _logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error"},
        )


def _register_gated_docs(app: FastAPI) -> None:
    """Serve OpenAPI docs only to basic-auth'd callers (don't expose the surface)."""
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.openapi.utils import get_openapi

    @app.get("/openapi.json", include_in_schema=False)
    def openapi(_: str = Depends(require_basic_auth)):
        return get_openapi(title=app.title, version=app.version, routes=app.routes)

    @app.get("/docs", include_in_schema=False)
    def docs(_: str = Depends(require_basic_auth)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} docs")


app = create_app()
