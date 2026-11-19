"""FastAPI application: routes, exception handlers, request-id middleware, and lifespan resources.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 4000
"""

from __future__ import annotations

import http.cookiejar
import logging
import re
import secrets
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Any

import certifi
import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.core.logging import configure_logging, register_secrets, request_id_var, sanitize_request_id
from app.platforms import get_enabled_platforms
from app.schemas import (
    ErrorResponse,
    PlatformInfo,
    PlatformsResponse,
    PostRequest,
    PostResponse,
    ValidationErrorResponse,
)
from app.services.idempotency import IdempotencyConflict, IdempotencyStore
from app.services.posting_service import PostingService
from app.services.rate_limit import RateLimiter

logger = logging.getLogger("app")

_IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")  # printable ASCII, no spaces

# Pydantic error types -> our stable error codes.
_TYPE_TO_CODE = {
    "missing": "missing",
    "string_type": "invalid_type",
    "extra_forbidden": "unknown_field",
    "json_invalid": "invalid_json",
    "model_attributes_type": "invalid_body",
    "dict_type": "invalid_body",
}
_TYPE_TO_MESSAGE = {
    "missing": "Field required.",
    "string_type": "Message must be a string.",
    "json_invalid": "Request body is not valid JSON.",
    "model_attributes_type": 'Request body must be a JSON object like {"message": "..."}.',
    "dict_type": 'Request body must be a JSON object like {"message": "..."}.',
}


def validation_response(details: list[dict[str, str]]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"success": False, "error": "Validation failed", "details": details})


def error_response(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": message}, headers=headers)


def _error_details(exc: RequestValidationError) -> list[dict[str, str]]:
    details = []
    for err in exc.errors():
        err_type = str(err.get("type", "value_error"))
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body", "header", "query")]
        if err_type == "extra_forbidden":
            field_name = loc[-1] if loc else "body"
            details.append(
                {
                    "field": field_name,
                    "code": "unknown_field",
                    "message": f"Unknown field '{field_name}'. Only 'message' is accepted.",
                }
            )
            continue
        if err_type == "json_invalid":
            details.append({"field": "body", "code": "invalid_json", "message": _TYPE_TO_MESSAGE["json_invalid"]})
            continue
        # Our custom validation codes (empty, too_long, no_link, ...) are raised on the model -> field "message".
        field_name = loc[-1] if loc else ("message" if err_type not in _TYPE_TO_CODE else "body")
        code = _TYPE_TO_CODE.get(err_type, err_type)
        message = _TYPE_TO_MESSAGE.get(err_type, str(err.get("msg", "Invalid value.")))
        details.append({"field": field_name, "code": code, "message": message})
    return details


@lru_cache(maxsize=1)
def shared_ssl_context() -> ssl.SSLContext:
    """Building an SSL context (loading the CA bundle) is slow; do it once per process."""
    return ssl.create_default_context(cafile=certifi.where())


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    # A cookie jar that refuses all cookies: verification GETs must stay anonymous.
    no_cookies = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_read_timeout, connect=settings.http_connect_timeout),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        follow_redirects=True,
        verify=shared_ssl_context(),
        cookies=no_cookies,
        headers={"User-Agent": "automated-posting/1.0"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    register_secrets(settings.secret_values())
    platforms = get_enabled_platforms(settings)  # fail fast on ENABLED_PLATFORMS typos

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = _build_http_client(settings)
        app.state.posting_service = PostingService(settings, platforms, client)
        app.state.idempotency = IdempotencyStore[PostResponse](settings.idempotency_ttl_seconds, settings.idempotency_max_entries)
        app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
        logger.info(
            "startup",
            extra={
                "dry_run": settings.dry_run,
                "timezone": settings.app_timezone,
                "platforms": {p.key: ("configured" if p.is_configured() else "missing credentials") for p in platforms},
            },
        )
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Publishes one short message with exactly one CTA link to every enabled platform "
            "(Mastodon, Bluesky, Telegram, Dev.to, GitHub Gist, ...) and reports each result independently."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.platforms = platforms

    # ---------------------------------------------------------------- middleware / handlers
    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = sanitize_request_id(request.headers.get("X-Request-ID"))
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled_error", extra={"path": request.url.path})
                response = error_response(500, "Internal server error")
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return validation_response(_error_details(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error_response(exc.status_code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _on_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", exc_info=exc)
        return error_response(500, "Internal server error")

    # ---------------------------------------------------------------- dependencies
    async def guard(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
        """Optional X-API-Key check, then the per-IP rate limit."""
        expected_key = settings.api_key.get_secret_value() if settings.api_key else ""
        if expected_key and not (x_api_key and secrets.compare_digest(x_api_key.encode(), expected_key.encode())):
            raise StarletteHTTPException(401, "Unauthorized")
        limiter: RateLimiter = request.app.state.rate_limiter
        wait = limiter.check(request.client.host if request.client else "unknown")
        if wait is not None:
            raise StarletteHTTPException(429, "Too many requests", headers={"Retry-After": str(max(1, int(wait) + 1))})

    # ---------------------------------------------------------------- routes
    @app.get("/health", tags=["meta"], summary="Liveness check")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/platforms", tags=["meta"], response_model=PlatformsResponse, summary="Enabled platforms and their configuration state"
    )
    async def list_platforms() -> PlatformsResponse:
        return PlatformsResponse(
            dry_run=settings.dry_run,
            platforms=[
                PlatformInfo(
                    key=p.key,
                    name=p.name,
                    configured=p.is_configured(),
                    missing_env=p.missing_env(),
                    public_verification=p.publicly_verifiable and settings.verify_public_access,
                )
                for p in platforms
            ],
        )

    @app.post(
        "/posting",
        tags=["posting"],
        response_model=PostResponse,
        summary="Publish a message with one CTA link to every enabled platform",
        responses={
            401: {"model": ErrorResponse, "description": "Missing/invalid X-API-Key (only when API_KEY is set)"},
            422: {"model": ValidationErrorResponse, "description": "Invalid input"},
            429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
            500: {"model": ErrorResponse, "description": "Unexpected server error"},
        },
        dependencies=[Depends(guard)],
    )
    async def posting(
        payload: PostRequest,
        request: Request,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", description="Optional. Replays the cached response for 10 minutes."),
        ] = None,
    ) -> Any:
        service: PostingService = request.app.state.posting_service
        if idempotency_key is None:
            return await service.publish_everywhere(payload.message, payload.link)

        if not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            return validation_response(
                [
                    {
                        "field": "Idempotency-Key",
                        "code": "invalid_idempotency_key",
                        "message": "Idempotency-Key must be 1-255 printable ASCII characters without spaces.",
                    }
                ]
            )
        store: IdempotencyStore[PostResponse] = request.app.state.idempotency
        try:
            result, replayed = await store.run(
                idempotency_key,
                payload.message,
                lambda: service.publish_everywhere(payload.message, payload.link, idempotency_key),
            )
        except IdempotencyConflict:
            return validation_response(
                [
                    {
                        "field": "Idempotency-Key",
                        "code": "idempotency_conflict",
                        "message": "This Idempotency-Key was already used with a different message.",
                    }
                ]
            )
        return JSONResponse(
            content=result.model_dump(mode="json"), headers={"Idempotent-Replayed": "true" if replayed else "false"}
        )

    return app


app = create_app()
