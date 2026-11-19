# Sample responses

> These show the **shape** of each response. URLs and IDs are illustrative.
> Captures from real live runs belong in [`evidence/`](evidence/).

## 200: all platforms succeeded

```json
{
  "success": true,
  "summary": { "total": 5, "succeeded": 5, "failed": 0 },
  "results": [
    { "platform": "Mastodon", "status": "success", "posted_url": "https://mastodon.social/@your_test_account/113501234567890123", "public_access": true, "date": "2026-11-18", "time": "10:30:01", "error": null },
    { "platform": "Bluesky", "status": "success", "posted_url": "https://bsky.app/profile/yourtest.bsky.social/post/3lb2xyzabc22k", "public_access": true, "date": "2026-11-18", "time": "10:30:01", "error": null },
    { "platform": "Telegram", "status": "success", "posted_url": "https://t.me/your_test_channel/12", "public_access": true, "date": "2026-11-18", "time": "10:30:00", "error": null },
    { "platform": "Dev.to", "status": "success", "posted_url": "https://dev.to/yourtest/check-out-our-latest-business-platform-1a2b", "public_access": true, "date": "2026-11-18", "time": "10:30:02", "error": null },
    { "platform": "GitHub Gist", "status": "success", "posted_url": "https://gist.github.com/yourtest/0123456789abcdef0123456789abcdef", "public_access": true, "date": "2026-11-18", "time": "10:30:01", "error": null }
  ]
}
```

## 200: partial failure (wrong Dev.to key)

```json
{
  "success": true,
  "summary": { "total": 5, "succeeded": 4, "failed": 1 },
  "results": [
    { "platform": "Mastodon", "status": "success", "posted_url": "https://mastodon.social/@your_test_account/113501234567890124", "public_access": true, "date": "2026-11-18", "time": "10:35:12", "error": null },
    { "platform": "Bluesky", "status": "success", "posted_url": "https://bsky.app/profile/yourtest.bsky.social/post/3lb2xyzabc33k", "public_access": true, "date": "2026-11-18", "time": "10:35:12", "error": null },
    { "platform": "Telegram", "status": "success", "posted_url": "https://t.me/your_test_channel/13", "public_access": true, "date": "2026-11-18", "time": "10:35:11", "error": null },
    { "platform": "Dev.to", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "AUTH_FAILED: Dev.to rejected the credentials (HTTP 401): unauthorized" },
    { "platform": "GitHub Gist", "status": "success", "posted_url": "https://gist.github.com/yourtest/fedcba9876543210fedcba9876543210", "public_access": true, "date": "2026-11-18", "time": "10:35:12", "error": null }
  ]
}
```

## 200: every platform failed (still 200 because the input was valid)

```json
{
  "success": false,
  "summary": { "total": 5, "succeeded": 0, "failed": 5 },
  "results": [
    { "platform": "Mastodon", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "NOT_CONFIGURED: missing MASTODON_ACCESS_TOKEN" },
    { "platform": "Bluesky", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "RATE_LIMITED: Bluesky rate limit reached (HTTP 429); retry after 300s" },
    { "platform": "Telegram", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "AUTH_FAILED: Telegram rejected the credentials (HTTP 403): Forbidden: bot is not a member of the channel chat" },
    { "platform": "Dev.to", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "UNKNOWN_OUTCOME: request timed out after being sent; the post may exist" },
    { "platform": "GitHub Gist", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "TIMEOUT: GitHub Gist did not finish within 30s; the post may exist" }
  ]
}
```

## 200: `DRY_RUN=true`

Every enabled platform reports `"status": "failed"` with `"error": "DRY_RUN: not published"`. A dry run never reports a fake success.

## 200: published but not verified

```json
{ "platform": "Dev.to", "status": "success", "posted_url": "https://dev.to/yourtest/post-1a2b", "public_access": false, "date": "2026-11-18", "time": "10:40:00", "error": null }
```

`public_access: false` on a success means **not verified**. The post was created, but an anonymous check could not
confirm it yet. Causes include propagation delay, a platform that blocks anonymous reads, or a login wall (LinkedIn, Discord).

## 422: validation failed

```json
{ "success": false, "error": "Validation failed",
  "details": [ { "field": "message", "code": "too_long", "message": "Message must not exceed 200 characters (got 240)." } ] }
```

| Input | `code` | `message` |
|---|---|---|
| `"   "` | `empty` | Message must not be empty. |
| 240 characters | `too_long` | Message must not exceed 200 characters (got 240). |
| `"No link here"` | `no_link` | Message must contain exactly one http(s) link (found none). |
| two links (duplicates count) | `multiple_links` | Message must contain exactly one link (found 2). |
| `www.example.com` | `invalid_url` | Include http:// or https:// in the link. |
| `ftp://…`, `javascript:…`, `mailto:…` | `invalid_url` | Only http and https links are allowed. |
| `https://localhost`, private IP, `user:pw@…` | `invalid_url` | The link is not a valid public http(s) URL: … |
| extra field `platforms` | `unknown_field` | Unknown field 'platforms'. Only 'message' is accepted. (`field` is `platforms`) |
| `{"message": 42}` | `invalid_type` | Message must be a string. |
| `{}` | `missing` | Field required. |
| malformed JSON | `invalid_json` | Request body is not valid JSON. (`field` is `body`) |
| reused `Idempotency-Key` with a different message | `idempotency_conflict` | This Idempotency-Key was already used with a different message. |

## Other errors

| Status | Body |
|---|---|
| 401 (only when `API_KEY` is set) | `{"success": false, "error": "Unauthorized"}` |
| 429 (per-IP limit) | `{"success": false, "error": "Too many requests"}` plus a `Retry-After` header |
| 500 | `{"success": false, "error": "Internal server error"}` (no stack trace) |
