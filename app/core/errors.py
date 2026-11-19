"""Typed platform errors. Every adapter failure is expressed as a `PlatformError`."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONTENT_TOO_LONG = "CONTENT_TOO_LONG"
    UNAVAILABLE = "UNAVAILABLE"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    DRY_RUN = "DRY_RUN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PlatformError(Exception):
    """A failure while publishing to one platform.

    Attributes:
        code: machine-readable prefix shown in the API response (``AUTH_FAILED: ...``).
        message: human-readable, already sanitized text (never contains secrets).
        retryable: the orchestrator's retry policy may try again.
        outcome_unknown: the request reached the platform but we cannot tell whether the post exists.
        retry_after: seconds the platform asked us to wait (429), if known.
        status_code: upstream HTTP status, if any.
        api_code: upstream error identifier (e.g. Bluesky ``ExpiredToken``), if any.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
        retry_after: float | None = None,
        status_code: int | None = None,
        api_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.retry_after = retry_after
        self.status_code = status_code
        self.api_code = api_code

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"
