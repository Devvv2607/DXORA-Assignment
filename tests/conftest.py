"""Shared fixtures. ALL tests are offline: HTTP is mocked with respx or replaced by fake adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.core.errors import PlatformError
from app.main import shared_ssl_context
from app.platforms.base import BasePlatform, PostOutcome

# Obviously fake credentials (they do not match real token formats, so the secret scanner ignores them).
FAKE_TELEGRAM_TOKEN = "123456789:FAKE_TOKEN_FOR_UNIT_TESTS_ONLY"
FAKE_ENV = {
    "mastodon_base_url": "https://mastodon.example",
    "mastodon_access_token": "fake-mastodon-token",
    "bluesky_handle": "tester.bsky.social",
    "bluesky_app_password": "fake-app-password",
    "telegram_bot_token": FAKE_TELEGRAM_TOKEN,
    "telegram_channel_username": "@test_channel",
    "devto_api_key": "fake-devto-key",
    "github_token": "fake-github-token",
}


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's real environment variables leak into tests."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def build_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "retry_base_delay_seconds": 0,
        "verify_retry_delay_seconds": 0,
        "rate_limit_per_minute": 0,
        **FAKE_ENV,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    return build_settings


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(verify=shared_ssl_context()) as http_client:
        yield http_client


@pytest.fixture
def mock_http() -> respx.MockRouter:
    """respx router that fails on any un-mocked request (proves nothing touches the network)."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


class FakePlatform(BasePlatform):
    """Configurable in-memory adapter for orchestration tests."""

    expected_hosts = {"fake.example"}

    def __init__(
        self,
        settings: Settings,
        key: str = "fake",
        *,
        delay: float = 0.0,
        error: Exception | None = None,
        verify: bool | Exception = True,
        required_env: list[str] | None = None,
    ) -> None:
        super().__init__(settings)
        self.key = key
        self.name = key.title()
        self.delay = delay
        self.error = error
        self.verify = verify
        self.required_env = required_env or []
        self.calls = 0

    async def publish(self, client, message, link, *, idempotency_key=None) -> PostOutcome:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return PostOutcome(posted_url=f"https://fake.example/{self.key}/1", platform_id="1")

    async def verify_public(self, client, outcome) -> bool:
        if isinstance(self.verify, Exception):
            raise self.verify
        return self.verify


__all__ = ["FAKE_ENV", "FAKE_TELEGRAM_TOKEN", "FakePlatform", "PlatformError", "build_settings"]
