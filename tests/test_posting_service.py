"""MOCKED/OFFLINE: orchestration tests with fake adapters and respx-mocked HTTP."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
import pytest
import respx

from app.core.errors import ErrorCode, PlatformError
from app.platforms.devto import DevtoPlatform
from app.platforms.mastodon import MastodonPlatform
from app.services.idempotency import IdempotencyConflict, IdempotencyStore
from app.services.posting_service import PostingService
from tests.conftest import FakePlatform

MESSAGE = "Grow your visibility https://example.com"
LINK = "https://example.com"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def service(settings, platforms, client) -> PostingService:
    return PostingService(settings, platforms, client)


async def test_all_platforms_succeed(settings, client) -> None:
    platforms = [FakePlatform(settings, k) for k in ("a", "b", "c")]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert response.success is True
    assert response.summary.model_dump() == {"total": 3, "succeeded": 3, "failed": 0}
    for result in response.results:
        assert result.status == "success"
        assert result.public_access is True
        assert DATE_RE.match(result.date)
        assert TIME_RE.match(result.time)
        assert result.error is None
        assert set(result.model_dump()) == {"platform", "status", "posted_url", "public_access", "date", "time", "error"}


async def test_one_fails_others_succeed(settings, client) -> None:
    platforms = [
        FakePlatform(settings, "a"),
        FakePlatform(settings, "b", error=PlatformError(ErrorCode.AUTH_FAILED, "B rejected the API key (HTTP 401)")),
        FakePlatform(settings, "c"),
    ]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert response.success is True
    assert response.summary.model_dump() == {"total": 3, "succeeded": 2, "failed": 1}
    failed = response.results[1]
    assert failed.status == "failed"
    assert failed.error == "AUTH_FAILED: B rejected the API key (HTTP 401)"
    assert failed.posted_url is None
    assert failed.date is None
    assert failed.time is None
    assert failed.public_access is False


async def test_all_fail_gives_success_false(settings, client) -> None:
    platforms = [FakePlatform(settings, k, error=PlatformError(ErrorCode.UNAVAILABLE, "down")) for k in ("a", "b")]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert response.success is False
    assert response.summary.succeeded == 0


async def test_platform_timeout_is_isolated(make_settings, client) -> None:
    settings = make_settings(platform_timeout_seconds=0.2)
    platforms = [FakePlatform(settings, "slow", delay=5), FakePlatform(settings, "fast")]
    started = time.perf_counter()
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert time.perf_counter() - started < 2
    assert response.results[0].error.startswith("TIMEOUT:")
    assert response.results[1].status == "success"


async def test_unexpected_exception_is_isolated(settings, client) -> None:
    platforms = [FakePlatform(settings, "boom", error=RuntimeError("secret internals")), FakePlatform(settings, "ok")]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].error == "INTERNAL_ERROR: unexpected error in the Boom adapter"
    assert "secret internals" not in response.results[0].error
    assert response.results[1].status == "success"


async def test_verification_error_never_turns_success_into_failure(settings, client) -> None:
    platforms = [FakePlatform(settings, "a", verify=RuntimeError("verify broke")), FakePlatform(settings, "b", verify=False)]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert [r.status for r in response.results] == ["success", "success"]
    assert [r.public_access for r in response.results] == [False, False]


async def test_missing_credentials_not_configured_without_http(make_settings, client, mock_http) -> None:
    settings = make_settings(mastodon_access_token=None, devto_api_key="")
    platforms = [MastodonPlatform(settings), DevtoPlatform(settings)]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].error == "NOT_CONFIGURED: missing MASTODON_ACCESS_TOKEN"
    assert response.results[1].error == "NOT_CONFIGURED: missing DEVTO_API_KEY"
    assert len(mock_http.calls) == 0


async def test_dry_run_never_calls_network_or_fakes_success(make_settings, client, mock_http) -> None:
    settings = make_settings(dry_run=True)
    response = await service(settings, [MastodonPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.success is False
    assert response.results[0].error == "DRY_RUN: not published"
    assert response.results[0].posted_url is None
    assert len(mock_http.calls) == 0


async def test_result_order_is_registry_order_not_completion_order(settings, client) -> None:
    platforms = [FakePlatform(settings, "first", delay=0.15), FakePlatform(settings, "second", delay=0.0)]
    response = await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    assert [r.platform for r in response.results] == ["First", "Second"]


async def test_platforms_run_concurrently(settings, client) -> None:
    platforms = [FakePlatform(settings, f"p{i}", delay=0.3) for i in range(4)]
    started = time.perf_counter()
    await service(settings, platforms, client).publish_everywhere(MESSAGE, LINK)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.6, f"expected ~0.3s (slowest platform), got {elapsed:.2f}s (sum would be 1.2s)"


async def test_dates_use_configured_timezone(make_settings, client) -> None:
    settings = make_settings(app_timezone="Asia/Kolkata")
    response = await service(settings, [FakePlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert DATE_RE.match(response.results[0].date)


# ------------------------------------------------------------------ retry policy (real adapters, mocked HTTP)

MASTODON_OK = {"id": "111", "url": "https://mastodon.example/@me/111", "visibility": "public"}


async def test_429_is_retried_then_succeeds(make_settings, client, mock_http) -> None:
    settings = make_settings(verify_public_access=False)
    route = mock_http.post("https://dev.to/api/articles").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(201, json={"id": 7, "url": "https://dev.to/me/post-7"}),
        ]
    )
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].status == "success"
    assert route.call_count == 2


async def test_429_with_long_retry_after_is_not_waited_for(make_settings, client, mock_http) -> None:
    settings = make_settings(retry_max_wait_seconds=10)
    route = mock_http.post("https://dev.to/api/articles").mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].error.startswith("RATE_LIMITED:")
    assert route.call_count == 1


async def test_5xx_on_non_idempotent_platform_is_not_retried(settings, client, mock_http) -> None:
    route = mock_http.post("https://dev.to/api/articles").mock(return_value=httpx.Response(502))
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert route.call_count == 1
    assert response.results[0].error.startswith("UNKNOWN_OUTCOME:")
    assert "may exist" in response.results[0].error


async def test_read_timeout_on_non_idempotent_platform_is_unknown_outcome(settings, client, mock_http) -> None:
    route = mock_http.post("https://dev.to/api/articles").mock(side_effect=httpx.ReadTimeout("timed out"))
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert route.call_count == 1
    assert response.results[0].error == "UNKNOWN_OUTCOME: request timed out after being sent; the post may exist"


async def test_connect_error_is_retried(make_settings, client, mock_http) -> None:
    settings = make_settings(verify_public_access=False)
    route = mock_http.post("https://dev.to/api/articles").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(201, json={"id": 1, "url": "https://dev.to/a/b"})]
    )
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].status == "success"
    assert route.call_count == 2


async def test_max_two_retries(settings, client, mock_http) -> None:
    route = mock_http.post("https://dev.to/api/articles").mock(side_effect=httpx.ConnectError("refused"))
    response = await service(settings, [DevtoPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert route.call_count == 3  # 1 attempt + 2 retries
    assert response.results[0].error.startswith("NETWORK_ERROR:")


async def test_mastodon_503_then_success_reuses_idempotency_key(make_settings, client, mock_http) -> None:
    settings = make_settings(verify_public_access=False)
    route = mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=MASTODON_OK)]
    )
    response = await service(settings, [MastodonPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].status == "success"
    assert route.call_count == 2
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert len(keys) == 1


async def test_mastodon_read_timeout_is_retried_with_same_key(make_settings, client, mock_http) -> None:
    settings = make_settings(verify_public_access=False)
    route = mock_http.post("https://mastodon.example/api/v1/statuses").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=MASTODON_OK)]
    )
    response = await service(settings, [MastodonPlatform(settings)], client).publish_everywhere(MESSAGE, LINK)
    assert response.results[0].status == "success"
    assert len({c.request.headers["Idempotency-Key"] for c in route.calls}) == 1


# ------------------------------------------------------------------ idempotency store


async def test_idempotency_store_replays_same_message() -> None:
    store: IdempotencyStore[str] = IdempotencyStore(ttl_seconds=600, max_entries=10)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "response"

    assert await store.run("k", "msg", factory) == ("response", False)
    assert await store.run("k", "msg", factory) == ("response", True)
    assert calls == 1


async def test_idempotency_store_conflict_on_different_message() -> None:
    store: IdempotencyStore[str] = IdempotencyStore(ttl_seconds=600, max_entries=10)

    async def factory() -> str:
        return "x"

    await store.run("k", "first", factory)
    with pytest.raises(IdempotencyConflict):
        await store.run("k", "second", factory)


async def test_idempotency_store_concurrent_requests_post_once() -> None:
    store: IdempotencyStore[str] = IdempotencyStore(ttl_seconds=600, max_entries=10)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "done"

    results = await asyncio.gather(*(store.run("k", "m", factory) for _ in range(5)))
    assert calls == 1
    assert sorted(r[1] for r in results) == [False, True, True, True, True]


async def test_idempotency_store_expires_and_is_bounded() -> None:
    now = [0.0]
    store: IdempotencyStore[str] = IdempotencyStore(ttl_seconds=10, max_entries=2, clock=lambda: now[0])

    async def factory() -> str:
        return "v"

    for key in ("a", "b", "c"):
        await store.run(key, "m", factory)
    assert len(store) == 2  # oldest evicted
    now[0] = 11
    assert (await store.run("b", "m", factory))[1] is False  # expired -> runs again


async def test_idempotency_store_does_not_cache_exceptions() -> None:
    store: IdempotencyStore[str] = IdempotencyStore(ttl_seconds=600, max_entries=10)

    async def boom() -> str:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await store.run("k", "m", boom)
    assert len(store) == 0


def test_respx_is_available() -> None:
    assert respx.__version__
