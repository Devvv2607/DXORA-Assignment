"""Platform registry. To add a platform: create one adapter module and add one line below."""

from __future__ import annotations

from app.config import Settings
from app.platforms.base import BasePlatform, PostOutcome
from app.platforms.bluesky import BlueskyPlatform
from app.platforms.devto import DevtoPlatform
from app.platforms.discord import DiscordPlatform
from app.platforms.github_gist import GistPlatform
from app.platforms.linkedin import LinkedInPlatform
from app.platforms.mastodon import MastodonPlatform
from app.platforms.telegram import TelegramPlatform
from app.platforms.wordpress import WordPressPlatform

# Order here is the order of results in every API response.
PLATFORM_REGISTRY: dict[str, type[BasePlatform]] = {
    MastodonPlatform.key: MastodonPlatform,
    BlueskyPlatform.key: BlueskyPlatform,
    TelegramPlatform.key: TelegramPlatform,
    DevtoPlatform.key: DevtoPlatform,
    GistPlatform.key: GistPlatform,
    WordPressPlatform.key: WordPressPlatform,
    LinkedInPlatform.key: LinkedInPlatform,
    DiscordPlatform.key: DiscordPlatform,
}


def get_enabled_platforms(settings: Settings) -> list[BasePlatform]:
    """Instantiate the platforms listed in ENABLED_PLATFORMS, in registry order. Fails fast on typos."""
    requested = settings.enabled_platform_keys
    unknown = sorted(set(requested) - PLATFORM_REGISTRY.keys())
    if unknown:
        raise ValueError(
            f"ENABLED_PLATFORMS contains unknown platform(s): {', '.join(unknown)}. Valid values: {', '.join(PLATFORM_REGISTRY)}"
        )
    return [cls(settings) for key, cls in PLATFORM_REGISTRY.items() if key in requested]


__all__ = ["PLATFORM_REGISTRY", "BasePlatform", "PostOutcome", "get_enabled_platforms"]
