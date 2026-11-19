"""Bluesky (AT Protocol), raw HTTP — no SDK.

* Auth:  com.atproto.server.createSession  https://docs.bsky.app/docs/api/com-atproto-server-create-session
* Post:  com.atproto.repo.createRecord     https://docs.bsky.app/docs/advanced-guides/posts
* Links: rich-text facets with UTF-8 *byte* offsets https://docs.bsky.app/docs/advanced-guides/post-richtext
"""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.config import Settings
from app.core.errors import ErrorCode, PlatformError
from app.core.timeutil import utc_iso_now
from app.platforms.base import BasePlatform, PostOutcome
from app.services import public_check

BLUESKY_MAX_GRAPHEMES = 300  # lexicon app.bsky.feed.post#text maxGraphemes
BLUESKY_MAX_BYTES = 3000  # lexicon app.bsky.feed.post#text maxLength (UTF-8 bytes)
PUBLIC_APPVIEW = "https://public.api.bsky.app"
_REAUTH_ERRORS = {"ExpiredToken", "InvalidToken", "AuthenticationRequired", "AuthMissing"}


@dataclass
class _Session:
    did: str
    handle: str
    access_jwt: str


def count_graphemes(text: str) -> int:
    """Approximate count of user-perceived characters (extended grapheme clusters).

    Combining marks, ZWJ sequences, variation selectors, emoji modifiers and tag characters extend the
    previous grapheme; two regional indicators form one flag. The result is always <= ``len(text)``.
    """
    count = 0
    after_zwj = False
    open_flag = False  # the current grapheme is a single, still-unpaired regional indicator
    for ch in text:
        cp = ord(ch)
        extends = (
            unicodedata.category(ch) in ("Mn", "Me", "Mc")
            or cp == 0x200D
            or 0xFE00 <= cp <= 0xFE0F
            or 0x1F3FB <= cp <= 0x1F3FF
            or 0xE0020 <= cp <= 0xE007F
        )
        regional = 0x1F1E6 <= cp <= 0x1F1FF
        if extends or after_zwj:
            pass
        elif regional and open_flag:
            open_flag = False
        else:
            count += 1
            open_flag = regional
        after_zwj = cp == 0x200D
    return count


def build_link_facets(text: str, link: str) -> list[dict]:
    """Facet making ``link`` clickable. Offsets are UTF-8 byte positions, as the AT Protocol requires."""
    index = text.find(link)
    if index < 0:
        return []
    byte_start = len(text[:index].encode("utf-8"))
    byte_end = byte_start + len(link.encode("utf-8"))
    return [
        {
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}],
        }
    ]


class BlueskyPlatform(BasePlatform):
    key = "bluesky"
    name = "Bluesky"
    required_env = ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"]
    expected_hosts = {"bsky.app", "public.api.bsky.app"}

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._session: _Session | None = None
        self._lock = asyncio.Lock()

    @property
    def service_url(self) -> str:
        return self.require_https(self.settings.bluesky_service_url, "BLUESKY_SERVICE_URL")

    async def _login(self, client: httpx.AsyncClient) -> _Session:
        response = await self._send(
            client,
            "POST",
            f"{self.service_url}/xrpc/com.atproto.server.createSession",
            idempotent=True,  # creating a session has no side effect worth protecting
            json={
                "identifier": self.env("BLUESKY_HANDLE").lstrip("@"),
                "password": self.env("BLUESKY_APP_PASSWORD"),
            },
        )
        data = self._json(response, self.name)
        if not data.get("accessJwt") or not data.get("did"):
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Bluesky createSession response is missing accessJwt/did")
        return _Session(did=data["did"], handle=data.get("handle") or self.env("BLUESKY_HANDLE"), access_jwt=data["accessJwt"])

    async def _get_session(self, client: httpx.AsyncClient, *, force: bool = False, stale: _Session | None = None) -> _Session:
        async with self._lock:  # one login even when many requests arrive at once
            if self._session is None or (force and self._session is stale):
                self._session = await self._login(client)
            return self._session

    async def _create_record(self, client: httpx.AsyncClient, session: _Session, record: dict) -> httpx.Response:
        return await self._send(
            client,
            "POST",
            f"{self.service_url}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {session.access_jwt}"},
            json={"repo": session.did, "collection": "app.bsky.feed.post", "record": record},
        )

    async def publish(
        self, client: httpx.AsyncClient, message: str, link: str, *, idempotency_key: str | None = None
    ) -> PostOutcome:
        if count_graphemes(message) > BLUESKY_MAX_GRAPHEMES:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"Bluesky allows at most {BLUESKY_MAX_GRAPHEMES} graphemes")
        if len(message.encode("utf-8")) > BLUESKY_MAX_BYTES:
            raise PlatformError(ErrorCode.CONTENT_TOO_LONG, f"Bluesky allows at most {BLUESKY_MAX_BYTES} bytes of text")
        record = {
            "$type": "app.bsky.feed.post",
            "text": message,
            "createdAt": utc_iso_now(),
            "facets": build_link_facets(message, link),
        }
        session = await self._get_session(client)
        try:
            response = await self._create_record(client, session, record)
        except PlatformError as exc:
            if exc.status_code not in (400, 401) or (exc.status_code == 400 and exc.api_code not in _REAUTH_ERRORS):
                raise
            # Cached token expired or was revoked: log in again exactly once and retry.
            session = await self._get_session(client, force=True, stale=session)
            response = await self._create_record(client, session, record)

        data = self._json(response, self.name)
        uri = data.get("uri")
        if not isinstance(uri, str) or not uri.startswith("at://"):
            raise PlatformError(ErrorCode.INVALID_RESPONSE, "Bluesky response did not include an at:// URI; the post may exist")
        rkey = uri.rstrip("/").rsplit("/", 1)[-1]
        handle = session.handle.lstrip("@")
        return PostOutcome(
            posted_url=f"https://bsky.app/profile/{handle}/post/{rkey}",
            platform_id=uri,
            extra={"cid": data.get("cid")},
        )

    async def check_credentials(self, client: httpx.AsyncClient) -> str | None:
        """com.atproto.server.createSession: creates a login session only, never a record."""
        async with self._lock:
            self._session = await self._login(client)
        return None

    async def verify_public(self, client: httpx.AsyncClient, outcome: PostOutcome) -> bool:
        """bsky.app is a client-rendered SPA (200 for anything), so ask the public AppView instead."""
        url = f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPosts?uris={quote(outcome.platform_id, safe='')}"

        def post_is_returned(response: httpx.Response) -> bool:
            posts = response.json().get("posts") or []
            return any(post.get("uri") == outcome.platform_id for post in posts)

        return await public_check.check_public(
            client,
            url,
            self.allowed_hosts(),
            predicate=post_is_returned,
            attempts=3,  # the AppView indexes new posts within a second or two
            retry_delay=self.settings.verify_retry_delay_seconds,
        )
