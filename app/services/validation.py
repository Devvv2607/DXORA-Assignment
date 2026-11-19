"""Message and CTA-link validation.

Pure functions, no I/O: the CTA link is *never* fetched. The order of checks is part of the API
contract (see README "Validation rules"):

1. trim, then ``empty``            4. strip trailing punctuation / unbalanced closers
2. ``too_long`` (> 200 code points) 5. ``no_link`` / ``multiple_links``
3. disallowed link-like tokens      6. strict URL validation -> ``invalid_url``
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

MAX_MESSAGE_LENGTH = 200

# http(s) candidates. `\S*` (not `\S+`) so that a bare "https://" is caught and reported as invalid.
_HTTP_URL_RE = re.compile(r"https?://\S*", re.IGNORECASE)
# Any other "scheme://" token, e.g. ftp://, ws://, chrome://. The look-behind anchors it to a token start.
_OTHER_SCHEME_RE = re.compile(r"(?<![\w.+-])([a-z][a-z0-9+.-]*)://\S*", re.IGNORECASE)
# Dangerous pseudo-schemes without "//". Requires a non-space right after ":" so "data: grows" is plain text.
_PSEUDO_SCHEME_RE = re.compile(r"(?<![\w.+-])(?:javascript|vbscript|data|mailto|file):\S+", re.IGNORECASE)
# "www.example.com" without a scheme. The look-behind skips the "www." inside "https://www.example.com".
_WWW_RE = re.compile(r"(?<![\w./:@-])www\.\S+", re.IGNORECASE)

_TRAILING_PUNCTUATION = ".,;:!?'\"”’»…"
_BRACKET_PAIRS = ((")", "("), ("]", "["), ("}", "{"), (">", "<"))

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")

_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class MessageValidationError(ValueError):
    """Raised with a stable machine-readable ``code`` and a human-readable ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedMessage:
    text: str  # trimmed message, otherwise exactly as the client sent it
    link: str  # the single CTA link, with trailing punctuation removed


def clean_url_candidate(token: str) -> str:
    """Strip trailing sentence punctuation and *unbalanced* closing brackets.

    ``https://en.wikipedia.org/wiki/Python_(language)`` keeps its balanced ``)``;
    ``(see https://example.com).`` becomes ``https://example.com``.
    """
    changed = True
    while changed and token:
        changed = False
        while token and token[-1] in _TRAILING_PUNCTUATION:
            token = token[:-1]
            changed = True
        for close, opening in _BRACKET_PAIRS:
            if token.endswith(close) and token.count(close) > token.count(opening):
                token = token[:-1]
                changed = True
    return token


def extract_links(text: str) -> list[str]:
    """Return cleaned http(s) link candidates; raise ``invalid_url`` for disallowed link-like tokens."""
    for match in _OTHER_SCHEME_RE.finditer(text):
        if match.group(1).lower() not in {"http", "https"}:
            raise MessageValidationError("invalid_url", "Only http and https links are allowed.")
    if _PSEUDO_SCHEME_RE.search(text):
        raise MessageValidationError("invalid_url", "Only http and https links are allowed.")
    if _WWW_RE.search(text):
        raise MessageValidationError("invalid_url", "Include http:// or https:// in the link.")
    return [clean_url_candidate(match.group(0)) for match in _HTTP_URL_RE.finditer(text)]


def _invalid(reason: str) -> MessageValidationError:
    return MessageValidationError("invalid_url", f"The link is not a valid public http(s) URL: {reason}.")


def _check_ip(host: str) -> bool:
    """Return True if host is an IP literal (and raise if it is not publicly routable)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local:
        raise _invalid("private, loopback, link-local and reserved addresses are not allowed")
    return True


def validate_url(url: str) -> str:
    """Strictly validate a single CTA URL. Never performs network I/O."""
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in url):
        raise _invalid("it contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError as exc:
        raise _invalid("it could not be parsed") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise MessageValidationError("invalid_url", "Only http and https links are allowed.")
    if "@" in parts.netloc:
        raise _invalid("credentials (user:password@) are not allowed in links")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise _invalid("it has no host name")
    if port == 0:
        raise _invalid("port 0 is not allowed")
    try:
        _URL_ADAPTER.validate_python(url)
    except ValidationError as exc:
        raise _invalid("it is malformed") from exc

    if _check_ip(host):
        return url
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        raise _invalid(f"host '{host}' is a local or internal name")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid("the host name is not a valid domain") from exc
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or len(labels) < 2 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise _invalid("the host name is not a valid domain")
    tld = labels[-1]
    if not (tld.startswith("xn--") or (tld.isalpha() and len(tld) >= 2)):
        raise _invalid("the host name must end with a valid top-level domain")
    return url


def validate_message(raw: object) -> ValidatedMessage:
    """Validate the request message and return the trimmed text plus its single CTA link."""
    if not isinstance(raw, str):
        raise MessageValidationError("invalid_type", "Message must be a string.")
    text = raw.strip()
    if not text:
        raise MessageValidationError("empty", "Message must not be empty.")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise MessageValidationError("too_long", f"Message must not exceed {MAX_MESSAGE_LENGTH} characters (got {len(text)}).")
    links = extract_links(text)
    if not links:
        raise MessageValidationError("no_link", "Message must contain exactly one http(s) link (found none).")
    if len(links) > 1:
        raise MessageValidationError("multiple_links", f"Message must contain exactly one link (found {len(links)}).")
    return ValidatedMessage(text=text, link=validate_url(links[0]))
