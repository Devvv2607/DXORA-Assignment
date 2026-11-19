"""MOCKED/OFFLINE: API-level tests through httpx.ASGITransport, with all platform HTTP mocked by respx."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.main import create_app
from tests.conftest import FAKE_TELEGRAM_TOKEN, build_settings

MESSAGE = "Check out our latest business platform and grow your online visibility. https://example.com"
AT_URI = "at://did:plc:abc/app.bsky.feed.post/3krkey"


@asynccontextmanager
async def api(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(build_settings(**overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client


def mock_all_platforms(mock_http, *, devto_status: int = 201) -> None:
    """Happy-path mocks for the 5 core platforms (publish + public verification)."""
    mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        return_value=httpx.Response(200, json={"id": "1", "url": "https://mastodon.example/@me/1", "visibility": "public"})
    )
    mock_http.get("https://mastodon.example/api/v1/statuses/1").mock(
        return_value=httpx.Response(200, json={"id": "1", "visibility": "public"})
    )
    mock_http.post("https://bsky.social/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc", "handle": "tester.bsky.social"})
    )
    mock_http.post("https://bsky.social/xrpc/com.atproto.repo.createRecord").mock(
        return_value=httpx.Response(200, json={"uri": AT_URI, "cid": "c"})
    )
    mock_http.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts").mock(
        return_value=httpx.Response(200, json={"posts": [{"uri": AT_URI}]})
    )
    mock_http.post(f"https://api.telegram.org/bot{FAKE_TELEGRAM_TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5, "chat": {"username": "test_channel"}}})
    )
    mock_http.get("https://t.me/s/test_channel").mock(return_value=httpx.Response(200, text='data-post="test_channel/5"'))
    devto_json = {"id": 9, "url": "https://dev.to/me/post-9"} if devto_status < 300 else {"error": "unauthorized"}
    mock_http.post("https://dev.to/api/articles").mock(return_value=httpx.Response(devto_status, json=devto_json))
    mock_http.get("https://dev.to/me/post-9").mock(return_value=httpx.Response(200))
    mock_http.post("https://api.github.com/gists").mock(
        return_value=httpx.Response(201, json={"id": "g1", "html_url": "https://gist.github.com/me/g1"})
    )
    mock_http.get("https://gist.github.com/me/g1").mock(return_value=httpx.Response(200))


async def test_health() -> None:
    async with api() as client:
        response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_request_id_generated_and_unsafe_ids_replaced() -> None:
    async with api() as client:
        generated = await client.get("/health")
        unsafe = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert len(generated.headers["X-Request-ID"]) == 32
    assert unsafe.headers["X-Request-ID"] != "bad id\nwith newline"


async def test_platforms_endpoint_never_returns_secrets() -> None:
    async with api(github_token=None) as client:
        response = await client.get("/platforms")
    data = response.json()
    assert data["dry_run"] is False
    assert [p["key"] for p in data["platforms"]] == ["mastodon", "bluesky", "telegram", "devto", "gist"]
    gist = data["platforms"][4]
    assert gist["configured"] is False
    assert gist["missing_env"] == ["GITHUB_TOKEN"]
    assert "fake-mastodon-token" not in response.text
    assert FAKE_TELEGRAM_TOKEN not in response.text


async def test_full_success(mock_http) -> None:
    mock_all_platforms(mock_http)
    async with api() as client:
        response = await client.post("/posting", json={"message": MESSAGE})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["summary"] == {"total": 5, "succeeded": 5, "failed": 0}
    assert [r["platform"] for r in data["results"]] == ["Mastodon", "Bluesky", "Telegram", "Dev.to", "GitHub Gist"]
    assert [r["posted_url"] for r in data["results"]] == [
        "https://mastodon.example/@me/1",
        "https://bsky.app/profile/tester.bsky.social/post/3krkey",
        "https://t.me/test_channel/5",
        "https://dev.to/me/post-9",
        "https://gist.github.com/me/g1",
    ]
    assert all(r["public_access"] is True for r in data["results"])


async def test_partial_failure_wrong_token(mock_http) -> None:
    mock_all_platforms(mock_http, devto_status=401)
    async with api() as client:
        response = await client.post("/posting", json={"message": MESSAGE})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["summary"] == {"total": 5, "succeeded": 4, "failed": 1}
    devto = data["results"][3]
    assert devto == {
        "platform": "Dev.to",
        "status": "failed",
        "posted_url": None,
        "public_access": False,
        "date": None,
        "time": None,
        "error": "AUTH_FAILED: Dev.to rejected the credentials (HTTP 401): unauthorized",
    }


async def test_total_failure_is_still_200(mock_http) -> None:
    async with api(dry_run=True) as client:
        response = await client.post("/posting", json={"message": MESSAGE})
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert len(mock_http.calls) == 0


@pytest.mark.parametrize(
    ("payload", "field", "code"),
    [
        ({"message": "   "}, "message", "empty"),
        ({"message": "x" * 300}, "message", "too_long"),
        ({"message": "no link here"}, "message", "no_link"),
        ({"message": "https://a.com https://b.com"}, "message", "multiple_links"),
        ({"message": "go https://localhost"}, "message", "invalid_url"),
        ({"message": "go https://example.com", "platforms": ["mastodon"]}, "platforms", "unknown_field"),
        ({"message": 42}, "message", "invalid_type"),
        ({}, "message", "missing"),
    ],
)
async def test_validation_envelope(payload: dict, field: str, code: str) -> None:
    async with api() as client:
        response = await client.post("/posting", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "Validation failed"
    assert {"field": field, "code": code} == {k: data["details"][0][k] for k in ("field", "code")}
    assert set(data["details"][0]) == {"field", "code", "message"}


async def test_invalid_json_body() -> None:
    async with api() as client:
        response = await client.post("/posting", content=b'{"message": ', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["details"][0]["code"] == "invalid_json"


async def test_too_long_message_reports_length() -> None:
    async with api() as client:
        response = await client.post("/posting", json={"message": "a" * 220 + " https://example.com"})
    assert response.json()["details"][0]["message"] == "Message must not exceed 200 characters (got 240)."


async def test_idempotency_replay_and_conflict(mock_http) -> None:
    mock_all_platforms(mock_http)
    async with api() as client:
        first = await client.post("/posting", json={"message": MESSAGE}, headers={"Idempotency-Key": "key-1"})
        calls_after_first = len(mock_http.calls)
        second = await client.post("/posting", json={"message": MESSAGE}, headers={"Idempotency-Key": "key-1"})
        conflict = await client.post(
            "/posting", json={"message": "Different https://example.com"}, headers={"Idempotency-Key": "key-1"}
        )
        no_key = await client.post("/posting", json={"message": MESSAGE})
    assert first.json() == second.json()
    assert first.headers["Idempotent-Replayed"] == "false"
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len(mock_http.calls) > calls_after_first  # only because of the no_key request
    assert conflict.status_code == 422
    assert conflict.json()["details"][0]["code"] == "idempotency_conflict"
    assert no_key.status_code == 200


async def test_idempotency_replay_makes_no_new_calls(mock_http) -> None:
    mock_all_platforms(mock_http)
    async with api() as client:
        await client.post("/posting", json={"message": MESSAGE}, headers={"Idempotency-Key": "k"})
        before = len(mock_http.calls)
        await client.post("/posting", json={"message": MESSAGE}, headers={"Idempotency-Key": "k"})
    assert len(mock_http.calls) == before


async def test_api_key_guard() -> None:
    async with api(api_key="s3cret-key", dry_run=True) as client:
        denied = await client.post("/posting", json={"message": MESSAGE})
        allowed = await client.post("/posting", json={"message": MESSAGE}, headers={"X-API-Key": "s3cret-key"})
    assert denied.status_code == 401
    assert denied.json() == {"success": False, "error": "Unauthorized"}
    assert allowed.status_code == 200


async def test_rate_limit() -> None:
    async with api(rate_limit_per_minute=2, dry_run=True) as client:
        codes = [(await client.post("/posting", json={"message": MESSAGE})).status_code for _ in range(3)]
        limited = await client.post("/posting", json={"message": MESSAGE})
    assert codes == [200, 200, 429]
    assert limited.json() == {"success": False, "error": "Too many requests"}
    assert int(limited.headers["Retry-After"]) >= 1


async def test_unexpected_error_returns_generic_500(monkeypatch) -> None:
    async def explode(*_: Any, **__: Any) -> None:
        raise RuntimeError("database password is hunter2")

    async with api() as client:
        transport_app: Callable = client._transport.app  # type: ignore[attr-defined]
        monkeypatch.setattr(transport_app.state.posting_service, "publish_everywhere", explode)
        response = await client.post("/posting", json={"message": MESSAGE})
    assert response.status_code == 500
    assert response.json() == {"success": False, "error": "Internal server error"}
    assert "hunter2" not in response.text


async def test_openapi_docs_available() -> None:
    async with api() as client:
        docs = await client.get("/docs")
        schema = (await client.get("/openapi.json")).json()
    assert docs.status_code == 200
    assert "/posting" in schema["paths"]
