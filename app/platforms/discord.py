"""Discord (optional) — Execute Webhook with ``?wait=true`` (https://discord.com/developers/docs/resources/webhook#execute-webhook).

A Discord message is only as public as its channel/server, and message links require login, so
``public_access`` is always ``false`` ("not verified"). The webhook URL is itself a secret.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome

DISCORD_MAX_CHARS = 2000
_WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}


class DiscordPlatform(BasePlatform):
    key = "discord"
    name = "Discord"
    required_env = ["DISCORD_WEBHOOK_URL", "DISCORD_GUILD_ID"]  # guild id is needed to build the message link
    expected_hosts = {"discord.com"}
    publicly_verifiable = False

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        if len(message) > DISCORD_MAX_CHARS:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"Discord allows at most {DISCORD_MAX_CHARS} characters")
        webhook = self.env("DISCORD_WEBHOOK_URL") or ""
        parts = urlsplit(webhook)
        if parts.scheme != "https" or parts.hostname not in _WEBHOOK_HOSTS or "/api/webhooks/" not in parts.path:
            raise PlatformError(
                ErrorCode.NOT_CONFIGURED, "DISCORD_WEBHOOK_URL must be a https://discord.com/api/webhooks/... URL"
            )
        response = await self._send(
            client,
            "POST",
            webhook,
            params={"wait": "true"},
            json={"content": message, "allowed_mentions": {"parse": []}},  # never ping @everyone
        )
        data = self._json(response, self.name)
        message_id, channel_id = data.get("id"), data.get("channel_id")
        if not message_id or not channel_id:
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Discord response did not include id/channel_id; the post may exist")
        guild_id = data.get("guild_id") or self.env("DISCORD_GUILD_ID")
        return PostOutcome(
            posted_url=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}", platform_id=str(message_id)
        )

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        return False  # requires a Discord login; visibility depends on server/channel permissions
