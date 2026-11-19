"""Mastodon — POST /api/v1/statuses (https://docs.joinmastodon.org/methods/statuses/#create)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from urllib.parse import quote, urlsplit

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome
from app.services import public_check

MASTODON_MAX_CHARS = 500  # default instance limit; URLs count as 23 characters


class MastodonPlatform(BasePlatform):
    key = "mastodon"
    name = "Mastodon"
    required_env = ["MASTODON_BASE_URL", "MASTODON_ACCESS_TOKEN"]
    idempotent = True  # Mastodon honours the Idempotency-Key header (kept for 1 hour)

    @property
    def base_url(self) -> str:
        return self.require_https(self.env("MASTODON_BASE_URL") or "", "MASTODON_BASE_URL")

    def allowed_hosts(self) -> set[str]:
        host = urlsplit(self.env("MASTODON_BASE_URL") or "").hostname
        return {host} if host else set()

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        if len(message) > MASTODON_MAX_CHARS:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"Mastodon allows at most {MASTODON_MAX_CHARS} characters")
        # One key per publish() call, reused across retries so a retried POST can never create a duplicate.
        # If the client sent an Idempotency-Key, derive from it so replays after a restart are also deduplicated.
        key = hashlib.sha256(f"mastodon:{idempotency_key}".encode()).hexdigest() if idempotency_key else uuid.uuid4().hex
        response = await self._send(
            client,
            "POST",
            f"{self.base_url}/api/v1/statuses",
            headers={
                "Authorization": f"Bearer {self.env('MASTODON_ACCESS_TOKEN')}",
                "Idempotency-Key": key,
            },
            json={"status": message, "visibility": "public"},
        )
        data = self._json(response, self.name)
        status_id = str(data.get("id") or "")
        url = data.get("url") or data.get("uri")
        if not status_id or not isinstance(url, str):
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Mastodon response did not include id/url; the post may exist")
        return PostOutcome(posted_url=url, platform_id=status_id, extra={"visibility": data.get("visibility")})

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        """Unauthenticated API read; falls back to the public page if the instance requires auth for the API."""
        api_url = f"{self.base_url}/api/v1/statuses/{quote(outcome.platform_id, safe='')}"
        for attempt in range(2):
            response = await public_check.fetch_public(client, api_url, self.allowed_hosts())
            if response is not None and response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    return False
                return str(data.get("id")) == outcome.platform_id and data.get("visibility") == "public"
            if response is not None and response.status_code in (401, 403):
                break  # instance blocks anonymous API reads (e.g. authorized-fetch); use the HTML page
            if attempt == 0:
                await asyncio.sleep(self.settings.verify_retry_delay_seconds)
        return await public_check.check_public(
            client, outcome.posted_url, self.allowed_hosts(), retry_delay=self.settings.verify_retry_delay_seconds
        )
