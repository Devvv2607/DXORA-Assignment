"""Orchestration: publish to every enabled platform concurrently, isolate failures, build the response."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import Settings
from app.core.errors import ErrorCode, PlatformError
from app.core.logging import preview, redact
from app.core.timeutil import now
from app.platforms.base import BasePlatform, PostOutcome
from app.schemas import PlatformResult, PostResponse, Summary

logger = logging.getLogger(__name__)


def _failed(platform: BasePlatform, error: str) -> PlatformResult:
    return PlatformResult(
        platform=platform.name, status="failed", posted_url=None, public_access=False, date=None, time=None, error=error
    )


class PostingService:
    def __init__(self, settings: Settings, platforms: list[BasePlatform], client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.platforms = platforms
        self.client = client

    async def publish_everywhere(self, message: str, link: str, idempotency_key: str | None = None) -> PostResponse:
        logger.info(
            "posting_started",
            extra={"platforms": [p.key for p in self.platforms], "message_preview": preview(message)},
        )
        results = await asyncio.gather(*(self._run_platform(p, message, link, idempotency_key) for p in self.platforms))
        succeeded = sum(1 for r in results if r.status == "success")
        return PostResponse(
            success=succeeded >= 1,
            summary=Summary(total=len(results), succeeded=succeeded, failed=len(results) - succeeded),
            results=list(results),  # gather preserves input order = registry order
        )

    async def _run_platform(self, platform: BasePlatform, message: str, link: str, idempotency_key: str | None) -> PlatformResult:
        """Never raises: every failure becomes a failed PlatformResult."""
        started = time.perf_counter()
        result = await self._attempt(platform, message, link, idempotency_key)
        error_code = result.error.split(":", 1)[0] if result.error else None
        logger.info(
            "platform_result",
            extra={
                "platform": platform.key,
                "status": result.status,
                "error_code": error_code,
                "public_access": result.public_access,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return result

    async def _attempt(self, platform: BasePlatform, message: str, link: str, idempotency_key: str | None) -> PlatformResult:
        if self.settings.dry_run:
            return _failed(platform, f"{ErrorCode.DRY_RUN}: not published")
        missing = platform.missing_env()
        if missing:
            return _failed(platform, f"{ErrorCode.NOT_CONFIGURED}: missing {', '.join(missing)}")

        timeout = self.settings.platform_timeout_seconds
        try:
            outcome = await asyncio.wait_for(
                platform.publish(self.client, message, link, idempotency_key=idempotency_key), timeout
            )
        except PlatformError as exc:
            return _failed(platform, redact(str(exc)))
        except TimeoutError:
            return _failed(
                platform,
                f"{ErrorCode.TIMEOUT}: {platform.name} did not finish within {timeout:g}s; the post may exist",
            )
        except Exception:
            logger.exception("platform_unexpected_error", extra={"platform": platform.key})
            return _failed(platform, f"{ErrorCode.INTERNAL_ERROR}: unexpected error in the {platform.name} adapter")

        published_date, published_time = now(self.settings.app_timezone)  # moment of confirmed publication
        public_access = await self._verify(platform, outcome)
        return PlatformResult(
            platform=platform.name,
            status="success",
            posted_url=outcome.posted_url,
            public_access=public_access,
            date=published_date,
            time=published_time,
            error=None,
        )

    async def _verify(self, platform: BasePlatform, outcome: PostOutcome) -> bool:
        """Verification problems only ever make public_access=false; they never fail the platform."""
        if not self.settings.verify_public_access or not platform.publicly_verifiable:
            return False
        try:
            return bool(
                await asyncio.wait_for(platform.verify_public(self.client, outcome), self.settings.verify_timeout_seconds)
            )
        except Exception as exc:  # includes TimeoutError
            logger.info("verification_error", extra={"platform": platform.key, "error_type": type(exc).__name__})
            return False
