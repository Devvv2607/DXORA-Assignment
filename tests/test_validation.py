"""MOCKED/OFFLINE: pure validation tests (no network)."""

from __future__ import annotations

import pytest

from app.services.validation import (
    MessageValidationError,
    clean_url_candidate,
    validate_message,
)

LINK = "https://example.com"


def code_of(message: object) -> str:
    with pytest.raises(MessageValidationError) as exc_info:
        validate_message(message)
    return exc_info.value.code


def test_valid_message() -> None:
    result = validate_message(f"Check out our latest business platform. {LINK}")
    assert result.link == LINK
    assert result.text == f"Check out our latest business platform. {LINK}"


@pytest.mark.parametrize("message", ["", "   ", "\n\t  "])
def test_empty_and_whitespace_only(message: str) -> None:
    assert code_of(message) == "empty"


def test_non_string_is_rejected() -> None:
    assert code_of(123) == "invalid_type"


def test_exactly_200_characters_passes() -> None:
    message = "a" * (200 - len(LINK) - 1) + " " + LINK
    assert len(message) == 200
    assert validate_message(message).link == LINK


def test_201_characters_fails_with_actual_length() -> None:
    message = "a" * (201 - len(LINK) - 1) + " " + LINK
    with pytest.raises(MessageValidationError) as exc_info:
        validate_message(message)
    assert exc_info.value.code == "too_long"
    assert "(got 201)" in exc_info.value.message


def test_200_only_after_trimming_passes() -> None:
    core = "a" * (200 - len(LINK) - 1) + " " + LINK
    result = validate_message("   " + core + "\n\n  ")
    assert result.text == core


def test_length_counts_code_points_not_bytes() -> None:
    message = "é" * (200 - len(LINK) - 1) + " " + LINK  # 2 bytes each in UTF-8
    assert validate_message(message).link == LINK


def test_no_link() -> None:
    assert code_of("Just some text with no link at all") == "no_link"


def test_two_different_links() -> None:
    assert code_of("Visit https://example.com and https://example.org") == "multiple_links"


def test_same_link_twice_counts_as_two() -> None:
    assert code_of("Visit https://example.com or https://example.com") == "multiple_links"


@pytest.mark.parametrize(
    "message",
    [
        "Click javascript:alert(1)",
        "Download ftp://x.com/file",
        "Mail me mailto:someone@example.com",
        "data:text/html,<script>alert(1)</script>",
        "Open file:///etc/passwd",
        "Socket ws://example.com/chat",
    ],
)
def test_disallowed_schemes(message: str) -> None:
    assert code_of(message) == "invalid_url"


def test_plain_words_with_colon_are_not_schemes() -> None:
    assert validate_message("Our data: it grows fast. https://example.com").link == LINK


def test_www_without_scheme() -> None:
    with pytest.raises(MessageValidationError) as exc_info:
        validate_message("Visit www.example.com today")
    assert exc_info.value.code == "invalid_url"
    assert "http://" in exc_info.value.message


def test_www_with_scheme_is_fine() -> None:
    assert validate_message("Visit https://www.example.com today").link == "https://www.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "http://localhost:8000/x",
        "https://printer.local",
        "https://service.internal",
        "http://127.0.0.1",
        "http://192.168.1.1",
        "http://10.0.0.5/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://0.0.0.0",
        "https://user:pw@example.com",
        "https://user@example.com",
        "https://",
        "https://example",
        "https://example.c",
        "https://exa_mple.com",
        "https://-bad.com",
        "https://192.168.1",
        "https://example.com:99999",
    ],
)
def test_invalid_urls(url: str) -> None:
    assert code_of(f"Look at {url} now") == "invalid_url"


def test_public_ip_is_allowed() -> None:
    assert validate_message("Server http://8.8.8.8/status").link == "http://8.8.8.8/status"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Read this: https://example.com.", LINK),
        ("Read this: https://example.com!", LINK),
        ("Is it https://example.com?", LINK),
        ('He said "https://example.com"', LINK),
        ("(see https://example.com)", LINK),
        ("(see https://example.com).", LINK),
        ("[https://example.com/a]", "https://example.com/a"),
        ("Docs https://en.wikipedia.org/wiki/Python_(language)", "https://en.wikipedia.org/wiki/Python_(language)"),
        ("(Docs https://en.wikipedia.org/wiki/Python_(language))", "https://en.wikipedia.org/wiki/Python_(language)"),
    ],
)
def test_trailing_punctuation_and_parentheses(message: str, expected: str) -> None:
    result = validate_message(message)
    assert result.link == expected
    assert result.text == message  # original text untouched


@pytest.mark.parametrize(
    "message",
    [
        "https://example.com is our site",
        "Our site https://example.com is great",
        "Our site is https://example.com",
    ],
)
def test_link_at_start_middle_end(message: str) -> None:
    assert validate_message(message).link == LINK


def test_emoji_and_unicode_text() -> None:
    message = "🚀 Grüße aus Köln — 新しいプラットフォーム https://example.com/路径?q=ä"
    result = validate_message(message)
    assert result.link == "https://example.com/路径?q=ä"


def test_internationalized_domain() -> None:
    assert validate_message("Shop https://bücher.de").link == "https://bücher.de"


def test_url_with_query_and_fragment() -> None:
    url = "https://example.com/path?utm_source=x&b=2#section"
    assert validate_message(f"Go {url}").link == url


def test_clean_url_candidate_keeps_balanced_brackets() -> None:
    assert clean_url_candidate("https://a.com/x_(y)") == "https://a.com/x_(y)"
    assert clean_url_candidate("https://a.com/x)") == "https://a.com/x"
    assert clean_url_candidate("https://a.com/x).,") == "https://a.com/x"
