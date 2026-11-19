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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.env('GITHUB_TOKEN')}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def check_credentials(self, client: httpx.AsyncClient) -> str | None:
        """GET /user; for classic tokens also require the `gist` scope in X-OAuth-Scopes."""
        response = await self._send(client, "GET", "https://api.github.com/user", idempotent=True, headers=self._headers())
        self._json(response, self.name)
        scopes_header = response.headers.get("x-oauth-scopes")
        if scopes_header is None:
            # Fine-grained tokens do not send X-OAuth-Scopes; the required "Gists: write" user permission
            # cannot be confirmed without creating a gist.
            return "fine-grained token: confirm the 'Gists' account permission is set to Read and write"
        scopes = {scope.strip() for scope in scopes_header.split(",") if scope.strip()}
        if "gist" not in scopes:
            raise PlatformError(ErrorCode.AUTH_FAILED, "GitHub token is valid but lacks the 'gist' scope")
        return None

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        response = await self._send(
            client,
            "POST",
            "https://api.github.com/gists",
            headers=self._headers(),
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
