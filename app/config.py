"""Application settings, loaded from environment variables and an optional `.env` file."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENABLED_PLATFORMS = "mastodon,bluesky,telegram,devto,gist"


class Settings(BaseSettings):
    """All runtime configuration. Credentials are `SecretStr` so they never appear in reprs or logs."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application -------------------------------------------------------------------------
    app_name: str = "Automated Multi-Platform Posting API"
    log_level: str = "INFO"
    log_file: str | None = None  # optional: also append JSON logs here (used by scripts/live_run.py)
    app_timezone: str = "UTC"
    enabled_platforms: str = DEFAULT_ENABLED_PLATFORMS
    dry_run: bool = False

    # --- Timeouts / retries ------------------------------------------------------------------
    platform_timeout_seconds: float = Field(default=30.0, gt=0)
    verify_timeout_seconds: float = Field(default=20.0, gt=0)
    http_connect_timeout: float = Field(default=5.0, gt=0)
    http_read_timeout: float = Field(default=15.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=0.5, ge=0)
    retry_max_wait_seconds: float = Field(default=10.0, ge=0)
    verify_public_access: bool = True
    verify_retry_delay_seconds: float = Field(default=1.0, ge=0)

    # --- Idempotency / abuse protection ------------------------------------------------------
    idempotency_ttl_seconds: int = Field(default=600, gt=0)
    idempotency_max_entries: int = Field(default=1000, gt=0)
    rate_limit_per_minute: int = Field(default=20, ge=0)  # 0 disables the limiter
    api_key: SecretStr | None = None  # when set, POST /posting requires the X-API-Key header

    # --- Mastodon ----------------------------------------------------------------------------
    mastodon_base_url: str = "https://mastodon.social"
    mastodon_access_token: SecretStr | None = None

    # --- Bluesky -----------------------------------------------------------------------------
    bluesky_service_url: str = "https://bsky.social"
    bluesky_handle: str | None = None
    bluesky_app_password: SecretStr | None = None

    # --- Telegram ----------------------------------------------------------------------------
    telegram_bot_token: SecretStr | None = None
    telegram_channel_username: str | None = None

    # --- Dev.to ------------------------------------------------------------------------------
    devto_api_key: SecretStr | None = None
    devto_tags: str = "automation,api,python"

    # --- GitHub Gist -------------------------------------------------------------------------
    github_token: SecretStr | None = None

    # --- Optional adapters (disabled unless listed in ENABLED_PLATFORMS) ---------------------
    wp_site_url: str | None = None
    wp_username: str | None = None
    wp_app_password: SecretStr | None = None

    linkedin_access_token: SecretStr | None = None
    linkedin_author_urn: str | None = None
    linkedin_version: str = "202611"  # latest per LinkedIn versioning docs (checked 2026-11-19)

    discord_webhook_url: SecretStr | None = None
    discord_guild_id: str | None = None

    @field_validator("app_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"APP_TIMEZONE '{value}' is not a valid IANA time zone") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return value

    @property
    def enabled_platform_keys(self) -> list[str]:
        return [key.strip().lower() for key in self.enabled_platforms.split(",") if key.strip()]

    @property
    def devto_tag_list(self) -> list[str]:
        return [tag.strip() for tag in self.devto_tags.split(",") if tag.strip()]

    def env_value(self, env_name: str) -> str | None:
        """Return the configured value for an env var name (e.g. ``MASTODON_ACCESS_TOKEN``), or None if blank."""
        value = getattr(self, env_name.lower(), None)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def secret_values(self) -> list[str]:
        """All non-empty secret values, used by the log/error redaction filter."""
        values: list[str] = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr) and value.get_secret_value().strip():
                values.append(value.get_secret_value().strip())
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()
