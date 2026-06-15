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
from app.config import settings, validate_production_settings
from app.db import run_migrations

# Feature routers — each self-contained under features/<name>/router.py
from app.dashboard.router import router as dashboard_router
from app.features.admin.router import router as admin_router
from app.features.attachments.router import router as attachments_router
from app.features.calcom.router import router as calcom_router
from app.features.discord.router import router as discord_router
from app.features.documents.router import router as documents_router
from app.features.email.router import router as email_router
from app.features.events.router import router as events_router
from app.features.finance.router import router as finance_router
from app.features.ingest.router import router as ingest_router
from app.features.media.router import router as media_router
from app.features.mcp.auth import MCPAuthMiddleware
from app.features.mcp.router import router as mcp_guide_router
from app.features.mcp.server import mcp, mcp_app
from app.features.meetings.router import router as meetings_router
from app.features.members.router import router as members_router
from app.features.people.router import router as people_router
from app.features.projects.router import router as projects_router
from app.features.public_api.router import router as public_router
from app.features.reviews.router import router as reviews_router
from app.features.sponsor_leads.router import router as sponsor_leads_router
from app.features.sponsor_packages.router import router as sponsor_packages_router
from app.features.sponsors.router import router as sponsors_router
from app.features.tasks.router import router as tasks_router
from app.features.website.router import router as website_router

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("dsec")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: bring the schema to head (skippable in serverless — see
    # RUN_MIGRATIONS_ON_STARTUP) and log readiness.
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_migrations()
    _logger.info("DSEC agent API started (db=%s)", settings.DATABASE_URL.split("@")[-1])
    # The mounted MCP app needs its session manager running for the lifetime of
    # the process (it powers /mcp). Stateless HTTP, so safe on serverless.
    async with mcp.session_manager.run():
        yield
    # Shutdown: nothing to tear down (no persistent in-process state).


def create_app() -> FastAPI:
    """Build and configure the FastAPI instance."""
    # Fail loudly at boot if a production deploy is misconfigured (default
    # secrets / SQLite). No-op outside Vercel.
    validate_production_settings()

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
    _register_request_size_limit(app)
    _register_gated_docs(app)

    # --- Mount features. One line each; order is cosmetic. ---
    app.include_router(email_router, prefix="/email", tags=["email"])
    app.include_router(public_router, prefix="/public", tags=["public"])
    app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
    app.include_router(events_router, prefix="/events-api", tags=["events"])
    # Reviews extend the events resource: /events-api/{event_id}/review-form (Tally).
    app.include_router(reviews_router, prefix="/events-api", tags=["reviews"])
    app.include_router(media_router, prefix="/media", tags=["media"])
    app.include_router(attachments_router, prefix="/attachments", tags=["attachments"])
    app.include_router(people_router, prefix="/people", tags=["people"])
    app.include_router(projects_router, prefix="/projects", tags=["projects"])
    app.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
    app.include_router(meetings_router, prefix="/meetings", tags=["meetings"])
    app.include_router(documents_router, prefix="/documents", tags=["documents"])
    app.include_router(sponsors_router, prefix="/sponsors", tags=["sponsors"])
    app.include_router(sponsor_packages_router, prefix="/sponsor-packages", tags=["sponsor-packages"])
    app.include_router(sponsor_leads_router, prefix="/sponsor-leads", tags=["sponsor-leads"])
    app.include_router(finance_router, prefix="/finance", tags=["finance"])
    app.include_router(members_router, prefix="/members", tags=["members"])
    app.include_router(website_router, prefix="/website", tags=["website"])
    app.include_router(mcp_guide_router, prefix="/mcp-setup", tags=["mcp"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
    app.include_router(discord_router, prefix="/discord", tags=["discord"])
    app.include_router(calcom_router, prefix="/calcom", tags=["calcom"])

    # Mount the MCP server (Starlette sub-app) behind API-key auth. Clients
    # connect to /mcp; the setup guide lives at /mcp-setup.
    app.mount("/mcp", MCPAuthMiddleware(mcp_app))

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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "invalid request", "errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        _logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error"},
        )


def _register_request_size_limit(app: FastAPI) -> None:
    """Reject oversized request bodies early (the TODO.md MAX_REQUEST_BYTES guard).

    A cheap DoS guard for the JSON/form API surface: anything declaring a
    ``Content-Length`` over ``MAX_REQUEST_BYTES`` is rejected with 413 before the
    body is read into memory. The multipart upload routes (`/media`,
    `/attachments`, `/ingest`) carry far larger files and enforce their own
    per-file caps, so they're exempt; `/mcp` is a streaming transport and is
    exempt too. Requests without a Content-Length (chunked) fall through to the
    handler, which still applies its own limits.
    """
    # Prefixes whose handlers accept large/streamed bodies and self-limit.
    exempt = ("/media", "/attachments", "/ingest", "/mcp")
    max_bytes = settings.MAX_REQUEST_BYTES

    @app.middleware("http")
    async def _limit_request_size(request: Request, call_next):
        if not request.url.path.startswith(exempt):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    too_big = int(content_length) > max_bytes
                except ValueError:
                    too_big = False
                if too_big:
                    return JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={"detail": "request body too large"},
                    )
        return await call_next(request)


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
