"""Base class for platform adapters, plus the shared HTTP retry / error-mapping logic."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.core.errors import ErrorCode, PlatformError
from app.core.logging import redact
from app.services import public_check

logger = logging.getLogger(__name__)

# Errors raised before any byte of the request reached the server: always safe to retry.
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Errors after the request was (possibly) delivered: the outcome is unknown for non-idempotent writes.
_MAYBE_SENT_ERRORS = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)


@dataclass
class PostOutcome:
    """What an adapter returns after a successful publish."""

    posted_url: str
    platform_id: str
    extra: dict[str, Any] = field(default_factory=dict)


def sanitize_api_message(text: str | None, limit: int = 200) -> str:
    """Make an upstream error message safe to show: redact secrets, strip control chars, truncate."""
    if not text:
        return ""
    text = redact(" ".join(str(text).split()))
    text = "".join(ch for ch in text if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def derive_title(message: str, link: str, max_length: int = 70, fallback: str = "Update") -> str:
    """Title for long-form platforms: the message without the link, cut cleanly at a word boundary."""
    text = message.replace(link, " ")
    text = re.sub(r"\(\s*\)|\[\s*\]|<\s*>", " ", text)  # brackets that only wrapped the link
    text = " ".join(text.split()).strip(" -–—:|·,;")
    if not text:
        return fallback
    if len(text) <= max_length:
        return text
    cut = text[: max_length - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" ,;:-–—.")
    return (cut or text[: max_length - 1]) + "…"


class BasePlatform(ABC):
    """One third-party platform.

    Subclasses set the class attributes and implement :meth:`publish`. They may override
    :meth:`verify_public` when a plain GET on the post URL is not a trustworthy signal.
    """

    key: str  # registry key used in ENABLED_PLATFORMS, e.g. "mastodon"
    name: str  # display name used in API results, e.g. "Mastodon"
    required_env: list[str] = []
    expected_hosts: set[str] = set()  # hosts posted_url / verification URLs may have (SSRF guard)
    idempotent: bool = False  # True only if the API honours an idempotency key
    publicly_verifiable: bool = True  # False when anonymous verification is impossible (login wall)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --- configuration -----------------------------------------------------------------------
    def env(self, name: str) -> str | None:
        return self.settings.env_value(name)

    def missing_env(self) -> list[str]:
        return [name for name in self.required_env if not self.env(name)]

    def is_configured(self) -> bool:
        return not self.missing_env()

    def allowed_hosts(self) -> set[str]:
        """Hosts that verification may contact. Override to add configured hosts (e.g. a Mastodon instance)."""
        return set(self.expected_hosts)

    # --- behaviour ---------------------------------------------------------------------------
    @abstractmethod
    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        """Publish and return the outcome, or raise :class:`PlatformError`."""

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        """Default: unauthenticated GET of the post URL must return 200."""
        return await public_check.check_public(
            client,
            outcome.posted_url,
            self.allowed_hosts(),
            retry_delay=self.settings.verify_retry_delay_seconds,
        )

    # --- shared HTTP plumbing ----------------------------------------------------------------
    def _extract_error(self, response: httpx.Response) -> tuple[str | None, str | None]:
        """Return (human message, api error code) from an error response body. Override per platform."""
        try:
            data = response.json()
        except ValueError:
            return None, None
        if not isinstance(data, dict):
            return None, None
        api_code = data.get("error") if isinstance(data.get("error"), str) else None
        for key in ("error_description", "message", "description", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value, api_code
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors[:3]), api_code
        if isinstance(errors, dict | str):
            return str(errors), api_code
        return None, api_code

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(value) if value else None
            return max(0.0, parsed.timestamp() - time.time()) if parsed else None

    def _map_status(self, response: httpx.Response, *, idempotent: bool) -> PlatformError:
        status = response.status_code
        raw_message, api_code = self._extract_error(response)
        detail = sanitize_api_message(raw_message)
        suffix = f": {detail}" if detail else ""
        common = {"status_code": status, "api_code": api_code}

        rate_limited = status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0")
        if rate_limited:
            retry_after = self._retry_after(response)
            hint = f"; retry after {int(retry_after)}s" if retry_after is not None else ""
            return PlatformError(
                ErrorCode.RATE_LIMITED,
                f"{self.name} rate limit reached (HTTP {status}){hint}",
                retryable=True,
                retry_after=retry_after,
                **common,
            )
        if status in (401, 403):
            return PlatformError(ErrorCode.AUTH_FAILED, f"{self.name} rejected the credentials (HTTP {status}){suffix}", **common)
        if status == 404:
            return PlatformError(ErrorCode.NOT_FOUND, f"{self.name} endpoint or resource not found (HTTP 404){suffix}", **common)
        if status >= 500:
            if idempotent:
                return PlatformError(
                    ErrorCode.UNAVAILABLE, f"{self.name} is unavailable (HTTP {status}){suffix}", retryable=True, **common
                )
            return PlatformError(
                ErrorCode.UNKNOWN_OUTCOME,
                f"{self.name} returned HTTP {status} after receiving the request; the post may exist",
                outcome_unknown=True,
                **common,
            )
        return PlatformError(ErrorCode.BAD_REQUEST, f"{self.name} rejected the request (HTTP {status}){suffix}", **common)

    def _backoff(self, attempt: int) -> float:
        base = self.settings.retry_base_delay_seconds
        return min(self.settings.retry_max_wait_seconds, base * (2**attempt)) + random.uniform(0, base)

    async def _send(
        self, client: httpx.AsyncClient, method: str, url: str, *, idempotent: bool | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Send one request under the strict retry policy and return a 2xx response, or raise PlatformError.

        * Connect-phase errors (request never sent) and 429 are retried.
        * 5xx / read timeouts are retried only when ``idempotent`` (same idempotency key on every attempt);
          otherwise they raise ``UNKNOWN_OUTCOME`` because the post may already exist.
        * Error text never includes the request URL or headers (they can contain tokens).
        """
        idempotent = self.idempotent if idempotent is None else idempotent
        attempt = 0
        while True:
            error: PlatformError
            try:
                response = await client.request(method, url, **kwargs)
            except _NOT_SENT_ERRORS as exc:
                code = ErrorCode.TIMEOUT if isinstance(exc, httpx.TimeoutException) else ErrorCode.NETWORK_ERROR
                error = PlatformError(code, f"could not connect to {self.name} ({type(exc).__name__})", retryable=True)
            except _MAYBE_SENT_ERRORS as exc:
                if not idempotent:
                    raise PlatformError(
                        ErrorCode.UNKNOWN_OUTCOME,
                        f"request {'timed out' if isinstance(exc, httpx.TimeoutException) else 'failed'} "
                        "after being sent; the post may exist",
                        outcome_unknown=True,
                    ) from None
                code = ErrorCode.TIMEOUT if isinstance(exc, httpx.TimeoutException) else ErrorCode.NETWORK_ERROR
                error = PlatformError(code, f"{self.name} did not respond ({type(exc).__name__})", retryable=True)
            except httpx.HTTPError as exc:
                raise PlatformError(
                    ErrorCode.NETWORK_ERROR, f"HTTP error talking to {self.name} ({type(exc).__name__})"
                ) from None
            else:
                if response.is_success:
                    return response
                error = self._map_status(response, idempotent=idempotent)

            if not error.retryable or attempt >= self.settings.max_retries:
                raise error
            delay = error.retry_after if error.retry_after is not None else self._backoff(attempt)
            if delay > self.settings.retry_max_wait_seconds:
                raise error  # the platform wants us to wait longer than we are willing to block a request
            attempt += 1
            logger.info(
                "platform_retry",
                extra={"platform": self.key, "attempt": attempt, "error_code": error.code.value, "delay_s": round(delay, 2)},
            )
            await asyncio.sleep(delay)

    @staticmethod
    def _json(response: httpx.Response, platform_name: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise PlatformError(
                ErrorCode.INVALID_RESPONSE,
                f"{platform_name} returned an unexpected response body (HTTP {response.status_code}); the post may exist",
                outcome_unknown=True,
            )
        return data

    @staticmethod
    def require_https(url: str, env_name: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise PlatformError(ErrorCode.NOT_CONFIGURED, f"{env_name} must be an https:// URL")
        return url.rstrip("/")
