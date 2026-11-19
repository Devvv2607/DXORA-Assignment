"""Telegram Bot API — sendMessage to a public channel (https://core.telegram.org/bots/api#sendmessage).

The bot token is part of the request URL, so error text here is built only from the status code and
the API's ``description`` (sanitized), never from the URL or the httpx exception string.
"""

from __future__ import annotations

import re

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome
from app.services import public_check

TELEGRAM_MAX_CHARS = 4096
_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def normalize_channel(value: str) -> str:
    value = value.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    return value.strip("/")


class TelegramPlatform(BasePlatform):
    key = "telegram"
    name = "Telegram"
    required_env = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_USERNAME"]
    expected_hosts = {"t.me"}

    @property
    def channel(self) -> str:
        channel = normalize_channel(self.env("TELEGRAM_CHANNEL_USERNAME") or "")
        if not _CHANNEL_RE.fullmatch(channel):
            raise PlatformError(ErrorCode.NOT_CONFIGURED, "TELEGRAM_CHANNEL_USERNAME must be a public channel username")
        return channel

    def _extract_error(self, response: httpx.Response) -> tuple[str | None, str | None]:
        try:
            data = response.json()
        except ValueError:
            return None, None
        return (data.get("description"), None) if isinstance(data, dict) else (None, None)

    def _retry_after(self, response: httpx.Response) -> float | None:
        try:
            value = (response.json().get("parameters") or {}).get("retry_after")
            if value is not None:
                return float(value)
        except (ValueError, AttributeError):
            pass
        return super()._retry_after(response)

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        if len(message) > TELEGRAM_MAX_CHARS:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"Telegram allows at most {TELEGRAM_MAX_CHARS} characters")
        channel = self.channel
        response = await self._send(
            client,
            "POST",
            f"https://api.telegram.org/bot{self.env('TELEGRAM_BOT_TOKEN')}/sendMessage",
            json={"chat_id": f"@{channel}", "text": message},  # no parse_mode: text is posted verbatim
        )
        data = self._json(response, self.name)
        result = data.get("result") or {}
        message_id = result.get("message_id")
        if not data.get("ok") or message_id is None:
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Telegram response did not include a message_id; the post may exist")
        username = (result.get("chat") or {}).get("username") or channel
        return PostOutcome(
            posted_url=f"https://t.me/{username}/{message_id}",
            platform_id=str(message_id),
            extra={"channel": username},
        )

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        """t.me/<channel>/<id> returns 200 even for missing ids, so check the public web preview instead."""
        channel = outcome.extra.get("channel") or self.channel
        marker = re.compile(re.escape(f"{channel}/{outcome.platform_id}") + r"(?!\d)", re.IGNORECASE)
        return await public_check.check_public(
            client,
            f"https://t.me/s/{channel}",
            self.allowed_hosts(),
            predicate=lambda response: bool(marker.search(response.text)),
            retry_delay=self.settings.verify_retry_delay_seconds,
        )
