"""LinkedIn (optional) — Posts API (https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api).

Requires an app with the "Share on LinkedIn" product (scope ``w_member_social``) and a member/organization
URN. LinkedIn content sits behind a login wall, so anonymous verification is impossible and
``public_access`` is always ``false`` ("not verified").
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.base import BasePlatform, PostOutcome

LINKEDIN_MAX_CHARS = 3000
# "little text format" reserved characters must be backslash-escaped in `commentary`.
_LITTLE_TEXT_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def escape_little_text(text: str) -> str:
    return _LITTLE_TEXT_RESERVED.sub(r"\\\1", text)


class LinkedInPlatform(BasePlatform):
    key = "linkedin"
    name = "LinkedIn"
    required_env = ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN"]
    expected_hosts = {"www.linkedin.com"}
    publicly_verifiable = False

    def _map_status(self, response: httpx.Response, *, idempotent: bool) -> PlatformError:
        error = super()._map_status(response, idempotent=idempotent)
        if response.status_code == 401:
            error.message = "LinkedIn access token is invalid or expired (HTTP 401); tokens last 60 days"
        return error

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        if len(message) > LINKEDIN_MAX_CHARS:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"LinkedIn allows at most {LINKEDIN_MAX_CHARS} characters")
        response = await self._send(
            client,
            "POST",
            "https://api.linkedin.com/rest/posts",
            headers={
                "Authorization": f"Bearer {self.env('LINKEDIN_ACCESS_TOKEN')}",
                "LinkedIn-Version": self.settings.linkedin_version,
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "author": self.env("LINKEDIN_AUTHOR_URN"),
                "commentary": escape_little_text(message),
                "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
        post_urn = response.headers.get("x-restli-id") or response.headers.get("x-linkedin-id")
        if not post_urn:
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "LinkedIn response did not include the post URN; the post may exist")
        return PostOutcome(posted_url=f"https://www.linkedin.com/feed/update/{quote(post_urn, safe=':')}/", platform_id=post_urn)

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        return False  # login wall: cannot be verified anonymously
