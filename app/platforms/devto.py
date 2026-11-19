"""Dev.to (Forem API v1) — POST /api/articles (https://developers.forem.com/api/v1#tag/articles)."""

from __future__ import annotations

import re

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome, derive_title

DEVTO_MAX_TAGS = 4


def clean_tags(tags: list[str]) -> list[str]:
    """Forem tags are lowercase alphanumerics; keep at most 4, without duplicates."""
    result: list[str] = []
    for tag in tags:
        tag = re.sub(r"[^a-z0-9]", "", tag.lower())
        if tag and tag not in result:
            result.append(tag)
    return result[:DEVTO_MAX_TAGS]


class DevtoPlatform(BasePlatform):
    key = "devto"
    name = "Dev.to"
    required_env = ["DEVTO_API_KEY"]
    expected_hosts = {"dev.to"}

    def build_article(self, message: str, link: str) -> dict:
        return {
            "article": {
                "title": derive_title(message, link),
                "body_markdown": message,
                "published": True,
                "tags": ",".join(clean_tags(self.settings.devto_tag_list)),  # Forem v1: comma-separated string
            }
        }

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.env("DEVTO_API_KEY") or "", "Accept": "application/vnd.forem.api-v1+json"}

    async def check_credentials(self, client: httpx.AsyncClient) -> str | None:
        """GET /api/users/me (200 with a valid api-key, 401 otherwise)."""
        response = await self._send(client, "GET", "https://dev.to/api/users/me", idempotent=True, headers=self._headers())
        self._json(response, self.name)
        return None

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        response = await self._send(
            client,
            "POST",
            "https://dev.to/api/articles",
            headers=self._headers(),
            json=self.build_article(message, link),
        )
        data = self._json(response, self.name)
        url = data.get("url")
        if not isinstance(url, str) or data.get("id") is None:
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Dev.to response did not include id/url; the post may exist")
        return PostOutcome(posted_url=url, platform_id=str(data["id"]))
