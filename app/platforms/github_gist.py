"""GitHub Gist (public) — POST /gists (https://docs.github.com/en/rest/gists/gists#create-a-gist)."""

from __future__ import annotations

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome, derive_title


class GistPlatform(BasePlatform):
    key = "gist"
    name = "GitHub Gist"
    required_env = ["GITHUB_TOKEN"]
    expected_hosts = {"gist.github.com"}

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        response = await self._send(
            client,
            "POST",
            "https://api.github.com/gists",
            headers={
                "Authorization": f"Bearer {self.env('GITHUB_TOKEN')}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "description": derive_title(message, link, max_length=120),
                "public": True,
                "files": {"message.md": {"content": message}},
            },
        )
        data = self._json(response, self.name)
        url = data.get("html_url")
        if not isinstance(url, str) or not data.get("id"):
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "GitHub response did not include id/html_url; the post may exist")
        return PostOutcome(posted_url=url, platform_id=str(data["id"]))
