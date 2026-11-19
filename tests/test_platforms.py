"""MOCKED/OFFLINE: adapter request/response contracts and public-access verification (respx)."""

from __future__ import annotations

import json
import logging
import re

import httpx
import pytest

from app.core.errors import ErrorCode, PlatformError
from app.core.logging import JsonFormatter, redact, register_secrets
from app.platforms import PLATFORM_REGISTRY, get_enabled_platforms
from app.platforms.base import PostOutcome, derive_title
from app.platforms.bluesky import BlueskyPlatform, build_link_facets, count_graphemes
from app.platforms.devto import DevtoPlatform, clean_tags
from app.platforms.discord import DiscordPlatform
from app.platforms.github_gist import GistPlatform
from app.platforms.linkedin import LinkedInPlatform, escape_little_text
from app.platforms.mastodon import MastodonPlatform
from app.platforms.telegram import TelegramPlatform, normalize_channel
from app.platforms.wordpress import WordPressPlatform
from app.services import public_check
from tests.conftest import FAKE_TELEGRAM_TOKEN

MESSAGE = "Check out our latest business platform and grow your online visibility. https://example.com"
LINK = "https://example.com"


def body(call) -> dict:
    return json.loads(call.request.content)


# ------------------------------------------------------------------ registry


def test_registry_order_and_enabled_filter(make_settings) -> None:
    settings = make_settings(enabled_platforms="gist, mastodon ,devto")
    assert [p.key for p in get_enabled_platforms(settings)] == ["mastodon", "devto", "gist"]
    assert list(PLATFORM_REGISTRY)[:5] == ["mastodon", "bluesky", "telegram", "devto", "gist"]


def test_unknown_platform_fails_fast(make_settings) -> None:
    with pytest.raises(ValueError, match="twitter"):
        get_enabled_platforms(make_settings(enabled_platforms="mastodon,twitter"))


# ------------------------------------------------------------------ Mastodon


async def test_mastodon_request_and_parsing(settings, client, mock_http) -> None:
    route = mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        return_value=httpx.Response(200, json={"id": "42", "url": "https://mastodon.example/@me/42", "visibility": "public"})
    )
    outcome = await MastodonPlatform(settings).publish(client, MESSAGE, LINK)
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer fake-mastodon-token"
    assert request.headers["Idempotency-Key"]
    assert body(route.calls[0]) == {"status": MESSAGE, "visibility": "public"}
    assert outcome.posted_url == "https://mastodon.example/@me/42"
    assert outcome.platform_id == "42"


async def test_mastodon_key_derived_from_client_idempotency_key(settings, client, mock_http) -> None:
    route = mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        return_value=httpx.Response(200, json={"id": "1", "url": "https://mastodon.example/@me/1"})
    )
    platform = MastodonPlatform(settings)
    await platform.publish(client, MESSAGE, LINK, idempotency_key="abc")
    await platform.publish(client, MESSAGE, LINK, idempotency_key="abc")
    assert route.calls[0].request.headers["Idempotency-Key"] == route.calls[1].request.headers["Idempotency-Key"]


async def test_mastodon_401_maps_to_auth_failed(settings, client, mock_http) -> None:
    mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        return_value=httpx.Response(401, json={"error": "The access token is invalid"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await MastodonPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert "The access token is invalid" in str(exc_info.value)


async def test_mastodon_requires_https_base_url(make_settings, client) -> None:
    with pytest.raises(PlatformError) as exc_info:
        await MastodonPlatform(make_settings(mastodon_base_url="http://insecure.example")).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.NOT_CONFIGURED


async def test_mastodon_verify_uses_unauthenticated_api(settings, client, mock_http) -> None:
    route = mock_http.get("https://mastodon.example/api/v1/statuses/42").mock(
        return_value=httpx.Response(200, json={"id": "42", "visibility": "public"})
    )
    outcome = PostOutcome(posted_url="https://mastodon.example/@me/42", platform_id="42")
    assert await MastodonPlatform(settings).verify_public(client, outcome) is True
    assert "authorization" not in route.calls[0].request.headers


async def test_mastodon_verify_falls_back_to_page_when_api_needs_auth(settings, client, mock_http) -> None:
    mock_http.get("https://mastodon.example/api/v1/statuses/42").mock(return_value=httpx.Response(401))
    page = mock_http.get("https://mastodon.example/@me/42").mock(return_value=httpx.Response(200, text="<html>"))
    outcome = PostOutcome(posted_url="https://mastodon.example/@me/42", platform_id="42")
    assert await MastodonPlatform(settings).verify_public(client, outcome) is True
    assert page.called


# ------------------------------------------------------------------ Bluesky

BSKY_SESSION = "https://bsky.social/xrpc/com.atproto.server.createSession"
BSKY_CREATE = "https://bsky.social/xrpc/com.atproto.repo.createRecord"
AT_URI = "at://did:plc:abc123/app.bsky.feed.post/3kxyzrkey"


def test_bluesky_facets_use_utf8_byte_offsets() -> None:
    text = "🚀 Grüße! https://example.com"
    facets = build_link_facets(text, LINK)
    prefix_bytes = len("🚀 Grüße! ".encode())  # 4 + 1 + 6 + 2 = 13 bytes, but only 9 characters
    assert prefix_bytes != len("🚀 Grüße! ")
    assert facets[0]["index"] == {"byteStart": prefix_bytes, "byteEnd": prefix_bytes + len(LINK)}
    assert text.encode()[prefix_bytes : prefix_bytes + len(LINK)].decode() == LINK
    assert facets[0]["features"] == [{"$type": "app.bsky.richtext.facet#link", "uri": LINK}]


async def test_bluesky_rejects_text_over_3000_bytes_without_network(settings, client, mock_http) -> None:
    """Lexicon app.bsky.feed.post#text: maxLength 3000 (bytes) in addition to maxGraphemes 300."""
    text = "👨‍👩‍👧" * 150 + " " + LINK  # 151 graphemes, 2,739+ bytes
    big = text + " " + "👨‍👩‍👧" * 20
    assert count_graphemes(big) <= 300
    assert len(big.encode()) > 3000
    with pytest.raises(PlatformError) as exc_info:
        await BlueskyPlatform(settings).publish(client, big, LINK)
    assert exc_info.value.code is ErrorCode.CONTENT_TOO_LONG
    assert len(mock_http.calls) == 0


def test_grapheme_count() -> None:
    assert count_graphemes("abc") == 3
    assert count_graphemes("é") == 1  # e + combining acute
    assert count_graphemes("👍🏽") == 1  # emoji + skin tone
    assert count_graphemes("👨‍👩‍👧") == 1  # ZWJ family
    assert count_graphemes("🇮🇳🇺🇸") == 2  # two flags


async def test_bluesky_login_post_and_url(settings, client, mock_http) -> None:
    session = mock_http.post(BSKY_SESSION).mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt-1", "did": "did:plc:abc123", "handle": "tester.bsky.social"})
    )
    create = mock_http.post(BSKY_CREATE).mock(return_value=httpx.Response(200, json={"uri": AT_URI, "cid": "c"}))
    platform = BlueskyPlatform(settings)
    outcome = await platform.publish(client, MESSAGE, LINK)

    assert body(session.calls[0]) == {"identifier": "tester.bsky.social", "password": "fake-app-password"}
    sent = body(create.calls[0])
    assert create.calls[0].request.headers["Authorization"] == "Bearer jwt-1"
    assert sent["repo"] == "did:plc:abc123"
    assert sent["collection"] == "app.bsky.feed.post"
    assert sent["record"]["text"] == MESSAGE
    assert sent["record"]["createdAt"].endswith("Z")
    assert sent["record"]["facets"][0]["features"][0]["uri"] == LINK
    assert outcome.posted_url == "https://bsky.app/profile/tester.bsky.social/post/3kxyzrkey"
    assert outcome.platform_id == AT_URI

    await platform.publish(client, MESSAGE, LINK)
    assert session.call_count == 1  # session is cached


async def test_bluesky_relogs_in_once_on_expired_token(settings, client, mock_http) -> None:
    session = mock_http.post(BSKY_SESSION).mock(
        side_effect=[
            httpx.Response(200, json={"accessJwt": "old", "did": "did:plc:abc123", "handle": "tester.bsky.social"}),
            httpx.Response(200, json={"accessJwt": "new", "did": "did:plc:abc123", "handle": "tester.bsky.social"}),
        ]
    )
    create = mock_http.post(BSKY_CREATE).mock(
        side_effect=[
            httpx.Response(400, json={"error": "ExpiredToken", "message": "Token has expired"}),
            httpx.Response(200, json={"uri": AT_URI, "cid": "c"}),
        ]
    )
    outcome = await BlueskyPlatform(settings).publish(client, MESSAGE, LINK)
    assert session.call_count == 2
    assert create.calls[1].request.headers["Authorization"] == "Bearer new"
    assert outcome.platform_id == AT_URI


async def test_bluesky_bad_app_password(settings, client, mock_http) -> None:
    mock_http.post(BSKY_SESSION).mock(
        return_value=httpx.Response(401, json={"error": "AuthenticationRequired", "message": "Invalid identifier or password"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await BlueskyPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED


async def test_bluesky_verify_uses_public_appview(settings, client, mock_http) -> None:
    route = mock_http.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts").mock(
        return_value=httpx.Response(200, json={"posts": [{"uri": AT_URI}]})
    )
    bsky_page = mock_http.get(url__startswith="https://bsky.app/")
    outcome = PostOutcome(posted_url="https://bsky.app/profile/x/post/3kxyzrkey", platform_id=AT_URI)
    assert await BlueskyPlatform(settings).verify_public(client, outcome) is True
    assert route.calls[0].request.url.params["uris"] == AT_URI
    assert not bsky_page.called  # a GET on the SPA would prove nothing


async def test_bluesky_verify_false_when_post_not_returned(settings, client, mock_http) -> None:
    mock_http.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts").mock(
        return_value=httpx.Response(200, json={"posts": []})
    )
    outcome = PostOutcome(posted_url="https://bsky.app/profile/x/post/r", platform_id=AT_URI)
    assert await BlueskyPlatform(settings).verify_public(client, outcome) is False


# ------------------------------------------------------------------ Telegram

TG_SEND = f"https://api.telegram.org/bot{FAKE_TELEGRAM_TOKEN}/sendMessage"


def test_normalize_channel() -> None:
    assert normalize_channel("@my_channel") == "my_channel"
    assert normalize_channel("https://t.me/my_channel") == "my_channel"


async def test_telegram_request_and_url(settings, client, mock_http) -> None:
    route = mock_http.post(TG_SEND).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 15, "chat": {"username": "Test_Channel"}}})
    )
    outcome = await TelegramPlatform(settings).publish(client, MESSAGE, LINK)
    assert body(route.calls[0]) == {"chat_id": "@test_channel", "text": MESSAGE}
    assert outcome.posted_url == "https://t.me/Test_Channel/15"


async def test_telegram_token_never_in_errors_or_logs(settings, client, mock_http, caplog) -> None:
    register_secrets(settings.secret_values())
    mock_http.post(TG_SEND).mock(
        return_value=httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": f"Unauthorized {FAKE_TELEGRAM_TOKEN}"}
        )
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert FAKE_TELEGRAM_TOKEN not in str(exc_info.value)
    assert "Unauthorized" in str(exc_info.value)

    mock_http.post(TG_SEND).mock(side_effect=httpx.ConnectError(f"failed to connect to {TG_SEND}"))
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).publish(client, MESSAGE, LINK)
    assert FAKE_TELEGRAM_TOKEN not in str(exc_info.value)

    record = logging.makeLogRecord({"msg": f"POST {TG_SEND} failed", "levelname": "INFO"})
    formatted = JsonFormatter().format(record)
    assert FAKE_TELEGRAM_TOKEN not in formatted
    assert all(FAKE_TELEGRAM_TOKEN not in r.getMessage() for r in caplog.records)


def test_redact_patterns() -> None:
    assert "<redacted>" in redact("https://api.telegram.org/bot987654321:" + "A" * 35 + "/send")
    assert "abc.def.ghi" not in redact("Authorization: Bearer abc.def.ghi")
    assert "s3cr3t" not in redact('{"api_key": "s3cr3t"}')
    assert "ghp_" not in redact("token ghp_" + "a" * 36)


async def test_telegram_429_reads_retry_after_from_body(make_settings, client, mock_http) -> None:
    settings = make_settings(max_retries=0)
    mock_http.post(TG_SEND).mock(
        return_value=httpx.Response(429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 7}})
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.RATE_LIMITED
    assert exc_info.value.retry_after == 7


async def test_telegram_verify_checks_channel_preview(settings, client, mock_http) -> None:
    mock_http.get("https://t.me/s/test_channel").mock(
        return_value=httpx.Response(200, text='<div data-post="test_channel/15"></div><div data-post="test_channel/150"></div>')
    )
    platform = TelegramPlatform(settings)
    ok = PostOutcome(posted_url="https://t.me/test_channel/15", platform_id="15", extra={"channel": "test_channel"})
    missing = PostOutcome(posted_url="https://t.me/test_channel/1", platform_id="1", extra={"channel": "test_channel"})
    assert await platform.verify_public(client, ok) is True
    assert await platform.verify_public(client, missing) is False  # "1" must not match "15"/"150"


# ------------------------------------------------------------------ Dev.to


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (MESSAGE, "Check out our latest business platform and grow your online…"),
        ("Short news https://example.com", "Short news"),
        ("https://example.com", "Update"),
        ("New launch (https://example.com)", "New launch"),
        ("Big news: https://example.com - read it", "Big news: - read it"),
    ],
)
def test_derive_title(message: str, expected: str) -> None:
    title = derive_title(message, LINK)
    assert title == expected
    assert len(title) <= 70


def test_clean_tags() -> None:
    assert clean_tags(["Python", "web-dev", "python", "AI", "news", "extra"]) == ["python", "webdev", "ai", "news"]


async def test_devto_request_and_parsing(make_settings, client, mock_http) -> None:
    settings = make_settings(devto_tags="automation,api,python,fastapi,extra")
    route = mock_http.post("https://dev.to/api/articles").mock(
        return_value=httpx.Response(201, json={"id": 99, "url": "https://dev.to/me/check-out-99"})
    )
    outcome = await DevtoPlatform(settings).publish(client, MESSAGE, LINK)
    request = route.calls[0].request
    assert request.headers["api-key"] == "fake-devto-key"
    assert request.headers["Accept"] == "application/vnd.forem.api-v1+json"
    article = body(route.calls[0])["article"]
    assert article["published"] is True
    assert article["body_markdown"] == MESSAGE
    assert LINK not in article["title"]
    assert article["tags"] == "automation,api,python,fastapi"  # Forem v1 schema: comma-separated string
    assert outcome.posted_url == "https://dev.to/me/check-out-99"


async def test_devto_422_maps_to_bad_request_with_message(settings, client, mock_http) -> None:
    mock_http.post("https://dev.to/api/articles").mock(
        return_value=httpx.Response(422, json={"error": "Title can't be blank", "status": 422})
    )
    with pytest.raises(PlatformError) as exc_info:
        await DevtoPlatform(settings).publish(client, MESSAGE, LINK)
    assert str(exc_info.value) == "BAD_REQUEST: Dev.to rejected the request (HTTP 422): Title can't be blank"


# ------------------------------------------------------------------ GitHub Gist


async def test_gist_request_and_parsing(settings, client, mock_http) -> None:
    route = mock_http.post("https://api.github.com/gists").mock(
        return_value=httpx.Response(201, json={"id": "abc", "html_url": "https://gist.github.com/me/abc"})
    )
    outcome = await GistPlatform(settings).publish(client, MESSAGE, LINK)
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer fake-github-token"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    sent = body(route.calls[0])
    assert sent["public"] is True
    assert sent["files"] == {"message.md": {"content": MESSAGE}}
    assert outcome.posted_url == "https://gist.github.com/me/abc"


async def test_gist_secondary_rate_limit_403_with_retry_after(make_settings, client, mock_http) -> None:
    """GitHub docs: secondary limits return 403 or 429; a 403 secondary limit carries retry-after."""
    settings = make_settings(max_retries=0)
    mock_http.post("https://api.github.com/gists").mock(
        return_value=httpx.Response(
            403, headers={"retry-after": "60"}, json={"message": "You have exceeded a secondary rate limit"}
        )
    )
    with pytest.raises(PlatformError) as exc_info:
        await GistPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.RATE_LIMITED
    assert exc_info.value.retry_after == 60


async def test_gist_plain_403_is_auth_failed(make_settings, client, mock_http) -> None:
    mock_http.post("https://api.github.com/gists").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await GistPlatform(make_settings(max_retries=0)).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED


async def test_gist_rate_limit_403(make_settings, client, mock_http) -> None:
    settings = make_settings(max_retries=0)
    mock_http.post("https://api.github.com/gists").mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await GistPlatform(settings).publish(client, MESSAGE, LINK)
    assert exc_info.value.code is ErrorCode.RATE_LIMITED


# ------------------------------------------------------------------ optional adapters


async def test_wordpress_request(make_settings, client, mock_http) -> None:
    settings = make_settings(wp_site_url="https://blog.example", wp_username="bot", wp_app_password="fake pass")
    route = mock_http.post("https://blog.example/wp-json/wp/v2/posts").mock(
        return_value=httpx.Response(201, json={"id": 5, "link": "https://blog.example/2026/11/post"})
    )
    outcome = await WordPressPlatform(settings).publish(client, MESSAGE, LINK)
    sent = body(route.calls[0])
    assert sent["status"] == "publish"
    assert f'<a href="{LINK}">{LINK}</a>' in sent["content"]
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")
    assert outcome.posted_url == "https://blog.example/2026/11/post"


async def test_linkedin_request_and_no_anonymous_verification(make_settings, client, mock_http) -> None:
    settings = make_settings(linkedin_access_token="fake-li", linkedin_author_urn="urn:li:person:abc")
    route = mock_http.post("https://api.linkedin.com/rest/posts").mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:123"})
    )
    platform = LinkedInPlatform(settings)
    outcome = await platform.publish(client, MESSAGE, LINK)
    request = route.calls[0].request
    assert request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert request.headers["LinkedIn-Version"] == settings.linkedin_version
    assert body(route.calls[0])["visibility"] == "PUBLIC"
    assert outcome.posted_url == "https://www.linkedin.com/feed/update/urn:li:share:123/"
    assert platform.publicly_verifiable is False
    assert escape_little_text("a (b) #c") == r"a \(b\) \#c"


async def test_discord_webhook(make_settings, client, mock_http) -> None:
    webhook = "https://discord.com/api/webhooks/1/fake"
    settings = make_settings(discord_webhook_url=webhook, discord_guild_id="9")
    route = mock_http.post(webhook).mock(return_value=httpx.Response(200, json={"id": "3", "channel_id": "2"}))
    outcome = await DiscordPlatform(settings).publish(client, MESSAGE, LINK)
    assert route.calls[0].request.url.params["wait"] == "true"
    assert body(route.calls[0])["allowed_mentions"] == {"parse": []}
    assert outcome.posted_url == "https://discord.com/channels/9/2/3"


# ------------------------------------------------------------------ public_check / SSRF guard


async def test_public_check_200_is_true(client, mock_http) -> None:
    route = mock_http.get("https://dev.to/me/post").mock(return_value=httpx.Response(200))
    assert await public_check.check_public(client, "https://dev.to/me/post", {"dev.to"}, retry_delay=0) is True
    request = route.calls[0].request
    assert "authorization" not in request.headers
    assert "api-key" not in request.headers
    assert "Mozilla" in request.headers["User-Agent"]


async def test_public_check_404_is_false_after_one_retry(client, mock_http) -> None:
    route = mock_http.get("https://dev.to/me/post").mock(return_value=httpx.Response(404))
    assert await public_check.check_public(client, "https://dev.to/me/post", {"dev.to"}, retry_delay=0) is False
    assert route.call_count == 2


async def test_public_check_follows_allowed_redirects(client, mock_http) -> None:
    mock_http.get("https://dev.to/a").mock(return_value=httpx.Response(301, headers={"location": "/b"}))
    mock_http.get("https://dev.to/b").mock(return_value=httpx.Response(200))
    assert await public_check.check_public(client, "https://dev.to/a", {"dev.to"}, retry_delay=0) is True


async def test_public_check_refuses_redirect_to_disallowed_host(client, mock_http) -> None:
    mock_http.get("https://dev.to/a").mock(return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    evil = mock_http.get(url__startswith="http://169.254.169.254")
    assert await public_check.check_public(client, "https://dev.to/a", {"dev.to"}, retry_delay=0) is False
    assert not evil.called


async def test_public_check_never_fetches_unlisted_host(client, mock_http) -> None:
    route = mock_http.get(url__startswith="https://example.com")
    assert await public_check.check_public(client, "https://example.com/", {"dev.to"}, retry_delay=0) is False
    assert not route.called


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://gist.github.com/x", True),
        ("http://gist.github.com/x", False),
        ("https://evil.com/x", False),
        ("https://gist.github.com.evil.com/x", False),
        ("https://127.0.0.1/x", False),
        ("https://user:pw@gist.github.com/x", False),
    ],
)
def test_is_allowed_url(url: str, allowed: bool) -> None:
    assert public_check.is_allowed_url(url, {"gist.github.com"}) is allowed


# ------------------------------------------------------------------ check_credentials (read-only, never posts)

TG_API = f"https://api.telegram.org/bot{FAKE_TELEGRAM_TOKEN}"
PUBLISH_ENDPOINTS = ("/api/v1/statuses", "createRecord", "sendMessage", "/api/articles", "/gists")


def mock_credential_endpoints(mock_http, *, gist_scopes: str | None = "gist, read:user") -> None:
    mock_http.get("https://mastodon.example/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(200, json={"id": "1", "username": "me"})
    )
    mock_http.post(BSKY_SESSION).mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc123", "handle": "tester.bsky.social"})
    )
    mock_http.get(f"{TG_API}/getMe").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"id": 42}}))
    mock_http.get(f"{TG_API}/getChat").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"id": -100, "type": "channel", "username": "test_channel"}})
    )
    mock_http.get(f"{TG_API}/getChatMember").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"status": "administrator", "can_post_messages": True}})
    )
    mock_http.get("https://dev.to/api/users/me").mock(return_value=httpx.Response(200, json={"id": 1, "username": "me"}))
    headers = {"x-oauth-scopes": gist_scopes} if gist_scopes is not None else {}
    mock_http.get("https://api.github.com/user").mock(return_value=httpx.Response(200, headers=headers, json={"login": "me"}))


async def test_check_credentials_is_read_only_for_all_core_platforms(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    for cls in (MastodonPlatform, BlueskyPlatform, TelegramPlatform, DevtoPlatform, GistPlatform):
        assert await cls(settings).check_credentials(client) is None
    for call in mock_http.calls:
        url = str(call.request.url).split("?")[0]
        assert not any(url.endswith(p) for p in PUBLISH_ENDPOINTS), url
        # Every call is a GET, except the Bluesky createSession login (which creates no record).
        assert call.request.method == "GET" or url.endswith("com.atproto.server.createSession"), url


async def test_mastodon_check_credentials_request_and_401(settings, client, mock_http) -> None:
    route = mock_http.get("https://mastodon.example/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(401, json={"error": "The access token is invalid"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await MastodonPlatform(settings).check_credentials(client)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert route.calls[0].request.headers["Authorization"] == "Bearer fake-mastodon-token"


async def test_bluesky_check_credentials_logs_in_only(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    create = mock_http.post(BSKY_CREATE)
    await BlueskyPlatform(settings).check_credentials(client)
    assert not create.called


async def test_telegram_check_credentials_requires_admin(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    mock_http.get(f"{TG_API}/getChatMember").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"status": "left"}})
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).check_credentials(client)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert "not an administrator" in str(exc_info.value)
    member_call = mock_http.calls[-1].request
    assert member_call.url.params["chat_id"] == "@test_channel"
    assert member_call.url.params["user_id"] == "42"


async def test_telegram_check_credentials_admin_without_post_permission(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    mock_http.get(f"{TG_API}/getChatMember").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"status": "administrator", "can_post_messages": False}})
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).check_credentials(client)
    assert "cannot post" in str(exc_info.value)


async def test_telegram_check_credentials_rejects_non_channel(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    mock_http.get(f"{TG_API}/getChat").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1, "type": "supergroup", "username": "g"}})
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).check_credentials(client)
    assert exc_info.value.code is ErrorCode.BAD_REQUEST


async def test_telegram_check_credentials_bad_token_never_leaks(settings, client, mock_http) -> None:
    register_secrets(settings.secret_values())
    mock_http.get(f"{TG_API}/getMe").mock(
        return_value=httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    )
    with pytest.raises(PlatformError) as exc_info:
        await TelegramPlatform(settings).check_credentials(client)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert FAKE_TELEGRAM_TOKEN not in str(exc_info.value)


async def test_devto_check_credentials_headers(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http)
    await DevtoPlatform(settings).check_credentials(client)
    request = mock_http.calls[-1].request
    assert request.headers["api-key"] == "fake-devto-key"
    assert request.headers["Accept"] == "application/vnd.forem.api-v1+json"


@pytest.mark.parametrize("scopes", ["gist, read:user", "gist"])
async def test_gist_check_credentials_classic_token_with_gist_scope(settings, client, mock_http, scopes) -> None:
    mock_credential_endpoints(mock_http, gist_scopes=scopes)
    assert await GistPlatform(settings).check_credentials(client) is None


async def test_gist_check_credentials_fine_grained_token_returns_note(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http, gist_scopes=None)
    note = await GistPlatform(settings).check_credentials(client)
    assert note is not None
    assert "fine-grained" in note


async def test_gist_check_credentials_missing_gist_scope(settings, client, mock_http) -> None:
    mock_credential_endpoints(mock_http, gist_scopes="repo, read:user")
    with pytest.raises(PlatformError) as exc_info:
        await GistPlatform(settings).check_credentials(client)
    assert exc_info.value.code is ErrorCode.AUTH_FAILED
    assert "gist" in str(exc_info.value)


# ------------------------------------------------------------------ scripts/check_credentials.py


def load_script(name: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_check_credentials_script_reports_each_platform(make_settings, client, mock_http) -> None:
    script = load_script("check_credentials")
    mock_credential_endpoints(mock_http)
    mock_http.get("https://dev.to/api/users/me").mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
    settings = make_settings(github_token=None, enabled_platforms="mastodon,devto,gist,wordpress")
    rows = await script.run_checks(get_enabled_platforms(settings), client)
    assert rows[0] == ("Mastodon", True, "OK")
    assert rows[1][1] is False
    assert rows[1][2].startswith("AUTH_FAILED:")
    assert rows[2] == ("GitHub Gist", False, "NOT_CONFIGURED: missing GITHUB_TOKEN")
    assert rows[3][2].startswith("NOT_CONFIGURED")  # optional adapter without credentials
    assert all(call.request.method == "GET" for call in mock_http.calls)


# ------------------------------------------------------------------ scripts/check_no_secrets.py


def test_secret_scanner_detects_token_shapes() -> None:
    scanner = load_script("check_no_secrets")
    samples = {
        "Telegram bot token": "123456789:" + "A" * 35,
        "GitHub token": "ghp_" + "a" * 36,
        "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "filled-in credential variable": "GITHUB_TOKEN=" + "x" * 12,
    }
    for name, text in samples.items():
        assert (1, name) in scanner.scan_text(text), name
    assert scanner.scan_text("message = 'Check out https://example.com'") == []


def test_env_example_placeholders_do_not_match_secret_patterns() -> None:
    from pathlib import Path

    scanner = load_script("check_no_secrets")
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert scanner.scan_text(text) == []


def test_secret_scanner_walk_mode_skips_dotenv(tmp_path) -> None:
    scanner = load_script("check_no_secrets")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=123456789:" + "B" * 35 + "\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    assert scanner.scan(tmp_path) == []  # .env is never scanned (it is never committed)
    (tmp_path / "leak.py").write_text("t = '123456789:" + "C" * 35 + "'\n", encoding="utf-8")
    assert scanner.scan(tmp_path) == [("leak.py", 1, "Telegram bot token")]
    assert scanner.main(["--root", str(tmp_path)]) == 1


def _template_text() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")


def test_clean_env_example_template_passes_full_scan(tmp_path) -> None:
    scanner = load_script("check_no_secrets")
    (tmp_path / ".env.example").write_text(_template_text(), encoding="utf-8")
    assert scanner.scan(tmp_path) == []  # .env.example is scanned, and the shipped template is clean


@pytest.mark.parametrize(
    "filled_line",
    [
        "DEVTO_API_KEY=abc123",  # plain non-empty value, no token shape
        'GITHUB_TOKEN="some-value"',  # quoted
        "MASTODON_ACCESS_TOKEN= spaced-value",  # space after =
        "export BLUESKY_APP_PASSWORD=value",  # shell export
        "TELEGRAM_BOT_TOKEN=123456789:" + "A" * 35,  # token-shaped value
    ],
)
def test_filled_in_env_example_fails_scan(tmp_path, filled_line) -> None:
    scanner = load_script("check_no_secrets")
    name = filled_line.removeprefix("export ").split("=", 1)[0]
    text = re.sub(rf"^{name}=.*$", filled_line, _template_text(), count=1, flags=re.MULTILINE)
    assert filled_line in text
    (tmp_path / ".env.example").write_text(text, encoding="utf-8")
    findings = scanner.scan(tmp_path)
    assert findings
    assert {rel for rel, _, _ in findings} == {".env.example"}
    assert scanner.main(["--root", str(tmp_path)]) == 1


@pytest.mark.parametrize("empty_line", ["DEVTO_API_KEY=", 'DEVTO_API_KEY=""', "DEVTO_API_KEY=   # set me"])
def test_empty_secret_values_pass(empty_line) -> None:
    scanner = load_script("check_no_secrets")
    assert scanner.scan_text(empty_line + "\n") == []
