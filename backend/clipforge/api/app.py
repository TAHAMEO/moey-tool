"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..logging_setup import configure_logging

log = logging.getLogger("clipforge.api")

API_PREFIX = "/api"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="CLIPFORGE AI",
        version="0.1.0",
        docs_url=None if settings.is_production else f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else f"{API_PREFIX}/openapi.json",
    )
    app.state.trusted_proxy_hops = settings.trusted_proxy_hops

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["content-type", "x-csrf-token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; media-src 'self'; img-src 'self'"
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:])
        message = f"{field}: {first.get('msg', 'invalid input')}" if field else first.get("msg", "invalid input")
        return JSONResponse({"error": {"code": "validation", "message": message}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "-")
        log.exception("Unhandled error request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            {"error": {"code": "internal", "message": "Something went wrong. The error was logged.", "request_id": request_id}},
            status_code=500,
        )

    from .routes import auth, health, providers
    from .routes import settings as settings_routes

    for module in (health, auth, providers, settings_routes):
        app.include_router(module.router, prefix=API_PREFIX)
    _include_optional_routers(app)
    return app


def _include_optional_routers(app: FastAPI) -> None:
    """Routers added by later phases; imported lazily to keep startup robust."""
    import importlib

    for name in ("chat", "projects", "jobs", "connections", "publishing", "analytics", "learning", "memory",
                 "media", "live", "admin", "library", "batches", "uploads", "local_ai"):
        try:
            module = importlib.import_module(f"clipforge.api.routes.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"clipforge.api.routes.{name}":
                continue
            raise
        app.include_router(module.router, prefix=API_PREFIX)
