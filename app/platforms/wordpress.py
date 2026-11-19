"""WordPress (optional) — REST API POST /wp-json/wp/v2/posts with an Application Password.

Docs: https://developer.wordpress.org/rest-api/reference/posts/#create-a-post
"""

from __future__ import annotations

import html
from urllib.parse import urlsplit

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome, derive_title


class WordPressPlatform(BasePlatform):
    key = "wordpress"
    name = "WordPress"
    required_env = ["WP_SITE_URL", "WP_USERNAME", "WP_APP_PASSWORD"]

    def allowed_hosts(self) -> set[str]:
        host = urlsplit(self.env("WP_SITE_URL") or "").hostname
        return {host} if host else set()

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        site = self.require_https(self.env("WP_SITE_URL") or "", "WP_SITE_URL")  # Basic auth only over HTTPS
        escaped_link = html.escape(link, quote=True)
        # Content is the original message; the link is wrapped in an anchor (text unchanged) so it is clickable.
        content = html.escape(message, quote=False).replace(
            html.escape(link, quote=False), f'<a href="{escaped_link}">{html.escape(link, quote=False)}</a>', 1
        )
        response = await self._send(
            client,
            "POST",
            f"{site}/wp-json/wp/v2/posts",
            auth=(self.env("WP_USERNAME") or "", self.env("WP_APP_PASSWORD") or ""),
            json={"title": derive_title(message, link), "content": content, "status": "publish"},
        )
        data = self._json(response, self.name)
        url = data.get("link")
        if not isinstance(url, str) or data.get("id") is None:
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "WordPress response did not include id/link; the post may exist")
        return PostOutcome(posted_url=url, platform_id=str(data["id"]))
