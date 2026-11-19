"""Read-only credential check for every enabled platform. NEVER posts anything.

Usage (from the project root):
    python scripts/check_credentials.py
    python scripts/check_credentials.py --platform telegram

Prints one line per enabled platform: ``OK``, ``OK (<note>)``, or ``<ERROR_CODE>: <message>``.
Exit code 0 if every checked platform is OK, 1 otherwise. Tokens are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core.errors import ErrorCode, PlatformError  # noqa: E402
from app.core.logging import configure_logging, redact, register_secrets  # noqa: E402
from app.main import _build_http_client  # noqa: E402
from app.platforms import BasePlatform, get_enabled_platforms  # noqa: E402

CHECK_TIMEOUT_SECONDS = 20.0


async def check_one(platform: BasePlatform, client: httpx.AsyncClient) -> tuple[bool, str]:
    """Return (ok, line) for one platform. Never raises."""
    missing = platform.missing_env()
    if missing:
        return False, f"{ErrorCode.NOT_CONFIGURED}: missing {', '.join(missing)}"
    try:
        note = await asyncio.wait_for(platform.check_credentials(client), CHECK_TIMEOUT_SECONDS)
    except NotImplementedError:
        return True, "SKIPPED: no read-only credential check for this adapter"
    except PlatformError as exc:
        return False, redact(str(exc))
    except TimeoutError:
        return False, f"{ErrorCode.TIMEOUT}: no answer within {CHECK_TIMEOUT_SECONDS:g}s"
    except Exception as exc:  # report, never crash the whole run
        return False, f"{ErrorCode.INTERNAL_ERROR}: {type(exc).__name__}"
    return True, f"OK ({note})" if note else "OK"


async def run_checks(platforms: list[BasePlatform], client: httpx.AsyncClient) -> list[tuple[str, bool, str]]:
    results = await asyncio.gather(*(check_one(p, client) for p in platforms))
    return [(p.name, ok, line) for p, (ok, line) in zip(platforms, results, strict=True)]


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only credential check (never posts).")
    parser.add_argument("--platform", help="check only this platform key (e.g. mastodon)")
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging("WARNING")
    register_secrets(settings.secret_values())
    platforms = get_enabled_platforms(settings)
    if args.platform:
        platforms = [p for p in platforms if p.key == args.platform.lower()]
        if not platforms:
            print(f"Platform '{args.platform}' is not enabled (ENABLED_PLATFORMS={settings.enabled_platforms}).")
            return 2
    if settings.dry_run:
        print("Note: DRY_RUN=true only blocks publishing; these checks are read-only and still run.")

    async with _build_http_client(settings) as client:
        rows = await run_checks(platforms, client)
    width = max((len(name) for name, _, _ in rows), default=8)
    for name, _, line in rows:
        print(f"{name.ljust(width)}  {line}")
    return 0 if all(ok for _, ok, _ in rows) else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
