"""Structured (JSON, one line per event) logging with request-id correlation and secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Patterns for credentials that may appear in text even if we did not put them there ourselves.
_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{10,}"), "bot<redacted>"),  # Telegram bot token in URLs
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "<redacted>"),  # bare Telegram bot token
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"), "Basic <redacted>"),
    (
        re.compile(r"(?i)((?:authorization|api[-_]?key|x-api-key|password|access[-_]?token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
        r"\1<redacted>",
    ),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "<redacted-jwt>"),
    (re.compile(r"(discord(?:app)?\.com/api/webhooks/)\d+/[A-Za-z0-9_-]+"), r"\1<redacted>"),
]

_known_secrets: set[str] = set()


def register_secrets(values: list[str]) -> None:
    """Register literal secret values (from settings) that must never be printed."""
    for value in values:
        if value and len(value) >= 4:
            _known_secrets.add(value)


def redact(text: str) -> str:
    """Remove credentials from arbitrary text."""
    if not text:
        return text
    for secret in sorted(_known_secrets, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def preview(text: str, limit: int = 40) -> str:
    """Short, single-line preview of user content for logs (never log the full message)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: str | None) -> str:
    """Accept a client-supplied X-Request-ID only if it is short and safe; otherwise generate one."""
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return new_request_id()


_RESERVED_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=True))


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """JSON logs to stdout, and additionally (appended) to ``log_file`` when set. Both are redacted."""
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, mode="a", encoding="utf-8"))
    root = logging.getLogger()
    for old in root.handlers:
        old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
    root.handlers[:] = handlers
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO (the Telegram URL contains the bot token). Keep it quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
