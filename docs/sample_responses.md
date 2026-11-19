# Sample responses

Every block below was **captured from an actual run** and is labeled with its source. None are hand-written:

- **Source: server** = real server on `localhost:4000` started with `DRY_RUN=true`, no credentials (captured 2026-11-19).
- **Source: in-process (mocked)** = real app in-process via `httpx.ASGITransport`; platform HTTP **mocked with respx**, so URLs are mock values and nothing was published (captured 2026-11-19). These show the exact response *shape* and error mapping; they are **not** evidence of live posting.
- **Live evidence** (real posts on real platforms) lives only in [`evidence/`](evidence/).

## 200: all platforms succeeded

**Source: in-process (mocked)**. All five platform APIs return success.

```http
HTTP 200
```

```json
{
  "success": true,
  "summary": {
    "total": 5,
    "succeeded": 5,
    "failed": 0
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "success",
      "posted_url": "https://mastodon.example/@me/1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Bluesky",
      "status": "success",
      "posted_url": "https://bsky.app/profile/tester.bsky.social/post/3krkey",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Telegram",
      "status": "success",
      "posted_url": "https://t.me/test_channel/5",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Dev.to",
      "status": "success",
      "posted_url": "https://dev.to/me/post-9",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "GitHub Gist",
      "status": "success",
      "posted_url": "https://gist.github.com/me/g1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    }
  ]
}
```

## 200: partial failure (wrong Dev.to key)

**Source: in-process (mocked)**. Dev.to returns HTTP 401; the other four are unaffected.

```http
HTTP 200
```

```json
{
  "success": true,
  "summary": {
    "total": 5,
    "succeeded": 4,
    "failed": 1
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "success",
      "posted_url": "https://mastodon.example/@me/1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Bluesky",
      "status": "success",
      "posted_url": "https://bsky.app/profile/tester.bsky.social/post/3krkey",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Telegram",
      "status": "success",
      "posted_url": "https://t.me/test_channel/5",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    },
    {
      "platform": "Dev.to",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "AUTH_FAILED: Dev.to rejected the credentials (HTTP 401): unauthorized"
    },
    {
      "platform": "GitHub Gist",
      "status": "success",
      "posted_url": "https://gist.github.com/me/g1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    }
  ]
}
```

## 200: every platform failed (input was valid, so still 200)

**Source: in-process (mocked)**. Mastodon has no token configured (no request is made); Bluesky returns 429 with `Retry-After: 300` (longer than the 10 s cap, so no retry); Telegram returns 403; Dev.to times out after the request was sent; GitHub returns 502 (not retried, because creating a gist is not idempotent).

```http
HTTP 200
```

```json
{
  "success": false,
  "summary": {
    "total": 5,
    "succeeded": 0,
    "failed": 5
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "NOT_CONFIGURED: missing MASTODON_ACCESS_TOKEN"
    },
    {
      "platform": "Bluesky",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "RATE_LIMITED: Bluesky rate limit reached (HTTP 429); retry after 300s"
    },
    {
      "platform": "Telegram",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "AUTH_FAILED: Telegram rejected the credentials (HTTP 403): Forbidden: bot is not a member of the channel chat"
    },
    {
      "platform": "Dev.to",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "UNKNOWN_OUTCOME: request timed out after being sent; the post may exist"
    },
    {
      "platform": "GitHub Gist",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "UNKNOWN_OUTCOME: GitHub Gist returned HTTP 502 after receiving the request; the post may exist"
    }
  ]
}
```

## 200: published but not verified (`public_access: false`)

**Source: in-process (mocked)**. The gist is created, but the anonymous GET of `html_url` returns 404. A success with `public_access: false` means **not verified**.

```http
HTTP 200
```

```json
{
  "success": true,
  "summary": {
    "total": 1,
    "succeeded": 1,
    "failed": 0
  },
  "results": [
    {
      "platform": "GitHub Gist",
      "status": "success",
      "posted_url": "https://gist.github.com/me/g1",
      "public_access": false,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    }
  ]
}
```

## 200: DRY_RUN

**Source: server**. A dry run never reports a fake success.

```http
HTTP 200
```

```json
{
  "success": false,
  "summary": {
    "total": 5,
    "succeeded": 0,
    "failed": 5
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "DRY_RUN: not published"
    },
    {
      "platform": "Bluesky",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "DRY_RUN: not published"
    },
    {
      "platform": "Telegram",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "DRY_RUN: not published"
    },
    {
      "platform": "Dev.to",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "DRY_RUN: not published"
    },
    {
      "platform": "GitHub Gist",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "DRY_RUN: not published"
    }
  ]
}
```

## Idempotency

**Source: in-process (mocked)**. Mastodon only. First request:

```http
HTTP 200
idempotent-replayed: false
```

```json
{
  "success": true,
  "summary": {
    "total": 1,
    "succeeded": 1,
    "failed": 0
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "success",
      "posted_url": "https://mastodon.example/@me/1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    }
  ]
}
```

Same key + same message: replayed from cache, no platform call made:

```http
HTTP 200
idempotent-replayed: true
```

```json
{
  "success": true,
  "summary": {
    "total": 1,
    "succeeded": 1,
    "failed": 0
  },
  "results": [
    {
      "platform": "Mastodon",
      "status": "success",
      "posted_url": "https://mastodon.example/@me/1",
      "public_access": true,
      "date": "2026-11-19",
      "time": "04:35:50",
      "error": null
    }
  ]
}
```

Same key + different message:

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "Idempotency-Key",
      "code": "idempotency_conflict",
      "message": "This Idempotency-Key was already used with a different message."
    }
  ]
}
```

## 422: validation errors

**Source: server**. One block per case:

### Empty after trimming

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "empty",
      "message": "Message must not be empty."
    }
  ]
}
```

### Too long (240 characters)

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "too_long",
      "message": "Message must not exceed 200 characters (got 240)."
    }
  ]
}
```

### No link

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "no_link",
      "message": "Message must contain exactly one http(s) link (found none)."
    }
  ]
}
```

### Two links

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "multiple_links",
      "message": "Message must contain exactly one link (found 2)."
    }
  ]
}
```

### Same link twice

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "multiple_links",
      "message": "Message must contain exactly one link (found 2)."
    }
  ]
}
```

### www without scheme

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "Include http:// or https:// in the link."
    }
  ]
}
```

### ftp scheme

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "Only http and https links are allowed."
    }
  ]
}
```

### javascript: pseudo-scheme

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "Only http and https links are allowed."
    }
  ]
}
```

### localhost

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "The link is not a valid public http(s) URL: host 'localhost' is a local or internal name."
    }
  ]
}
```

### Private IP

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "The link is not a valid public http(s) URL: private, loopback, link-local and reserved addresses are not allowed."
    }
  ]
}
```

### Credentials in URL

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_url",
      "message": "The link is not a valid public http(s) URL: credentials (user:password@) are not allowed in links."
    }
  ]
}
```

### Client tries to choose platforms (unknown field)

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "platforms",
      "code": "unknown_field",
      "message": "Unknown field 'platforms'. Only 'message' is accepted."
    }
  ]
}
```

### Message is not a string

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "invalid_type",
      "message": "Message must be a string."
    }
  ]
}
```

### Missing message

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "message",
      "code": "missing",
      "message": "Field required."
    }
  ]
}
```

### Malformed JSON

```http
HTTP 422
```

```json
{
  "success": false,
  "error": "Validation failed",
  "details": [
    {
      "field": "body",
      "code": "invalid_json",
      "message": "Request body is not valid JSON."
    }
  ]
}
```

## 401: missing X-API-Key (only when `API_KEY` is set)

**Source: in-process**, `API_KEY` set.

```http
HTTP 401
```

```json
{
  "success": false,
  "error": "Unauthorized"
}
```

## 429: per-IP rate limit

**Source: in-process**, `RATE_LIMIT_PER_MINUTE=1`, second request.

```http
HTTP 429
retry-after: 60
```

```json
{
  "success": false,
  "error": "Too many requests"
}
```

## 500: unexpected error

**Source: in-process (mocked)**. The posting service was replaced by one that raises; no stack trace or exception text reaches the client.

```http
HTTP 500
```

```json
{
  "success": false,
  "error": "Internal server error"
}
```

## GET /platforms

**Source: server** (no credentials configured).

```http
HTTP 200
```

```json
{
  "dry_run": true,
  "platforms": [
    {
      "key": "mastodon",
      "name": "Mastodon",
      "configured": false,
      "missing_env": [
        "MASTODON_ACCESS_TOKEN"
      ],
      "public_verification": true
    },
    {
      "key": "bluesky",
      "name": "Bluesky",
      "configured": false,
      "missing_env": [
        "BLUESKY_HANDLE",
        "BLUESKY_APP_PASSWORD"
      ],
      "public_verification": true
    },
    {
      "key": "telegram",
      "name": "Telegram",
      "configured": false,
      "missing_env": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHANNEL_USERNAME"
      ],
      "public_verification": true
    },
    {
      "key": "devto",
      "name": "Dev.to",
      "configured": false,
      "missing_env": [
        "DEVTO_API_KEY"
      ],
      "public_verification": true
    },
    {
      "key": "gist",
      "name": "GitHub Gist",
      "configured": false,
      "missing_env": [
        "GITHUB_TOKEN"
      ],
      "public_verification": true
    }
  ]
}
```
