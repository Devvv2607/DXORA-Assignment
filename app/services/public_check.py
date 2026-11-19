"""Unauthenticated public-accessibility checks, with an SSRF guard.

Only URLs produced by a platform adapter are ever fetched, and only if their host is on that
adapter's allow-list. Redirects are followed manually so every hop is re-checked. The user's CTA
link is never passed here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)

BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_PUBLIC_HEADERS = {"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}


def host_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == allowed or host.endswith("." + allowed) for allowed in (h.lower() for h in allowed_hosts))


def is_allowed_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    """https only, host on the allow-list, and never an IP literal (so never a private address)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # adapters never return IP-literal post URLs
    except ValueError:
        pass
    return host_allowed(host, allowed_hosts)


async def fetch_public(
    client: httpx.AsyncClient,
    url: str,
    allowed_hosts: Iterable[str],
    *,
    request_timeout: float = 10.0,
    max_redirects: int = 3,
) -> httpx.Response | None:
    """GET ``url`` with no credentials. Returns the final response, or None if blocked/failed."""
    allowed = set(allowed_hosts)
    current = url
    for _ in range(max_redirects + 1):
        if not is_allowed_url(current, allowed):
            logger.warning("public_check_blocked", extra={"host": urlsplit(current).hostname})
            return None
        try:
            response = await client.get(current, headers=_PUBLIC_HEADERS, follow_redirects=False, timeout=request_timeout)
        except httpx.HTTPError as exc:
            logger.info("public_check_network_error", extra={"error_type": type(exc).__name__})
            return None
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return None
            current = urljoin(current, location)
            continue
        return response
    logger.info("public_check_too_many_redirects")
    return None


async def check_public(
    client: httpx.AsyncClient,
    url: str,
    allowed_hosts: Iterable[str],
    *,
    predicate: Callable[[httpx.Response], bool] | None = None,
    attempts: int = 2,
    retry_delay: float = 1.0,
) -> bool:
    """True only if a final HTTP 200 is returned (and ``predicate`` accepts it). Retries for propagation delay."""
    for attempt in range(attempts):
        response = await fetch_public(client, url, allowed_hosts)
        if response is not None and response.status_code == 200:
            try:
                if predicate is None or predicate(response):
                    return True
            except Exception:  # a malformed body must not crash verification
                logger.info("public_check_predicate_error")
        elif response is None and not is_allowed_url(url, allowed_hosts):
            return False  # blocked by the SSRF guard: retrying cannot help
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_delay)
    return False
