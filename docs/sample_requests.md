# Sample requests

Every response below was **captured from an actual run**, not written by hand. Each block names its source:

- **Source: server** = real server on `localhost:4000` started with `DRY_RUN=true`, no credentials (captured 2026-11-19). With `DRY_RUN=true` nothing is published, so platform results are `DRY_RUN: not published`. Validation, routing and error envelopes are exactly what a live server returns.
- **Source: in-process (mocked)** = real app in-process via `httpx.ASGITransport`; platform HTTP **mocked with respx**, so URLs are mock values and nothing was published (captured 2026-11-19).

Start the server first:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

> Live runs publish real posts. Use dedicated test accounts and only `https://example.com` as the CTA link.
> For a live run with evidence capture use `python scripts/live_run.py --label live_success` (see README).

## POST /posting (valid message)

bash:

```bash
curl -s -X POST http://localhost:4000/posting \
  -H "Content-Type: application/json" \
  -d '{"message": "Check out our latest business platform and grow your online visibility. https://example.com"}'
```

PowerShell:

```powershell
$body = @{ message = "Check out our latest business platform and grow your online visibility. https://example.com" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:4000/posting -ContentType 'application/json' -Body $body |
  ConvertTo-Json -Depth 5
```

Response (**source: server**, `DRY_RUN=true`):

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

For live and mocked success/partial-failure responses see [sample_responses.md](sample_responses.md).

## POST /posting with an Idempotency-Key

bash:

```bash
curl -s -i -X POST http://localhost:4000/posting \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: docs-demo-001" \
  -d '{"message": "Check out our latest business platform and grow your online visibility. https://example.com"}'
```

PowerShell:

```powershell
$body = @{ message = "Check out our latest business platform and grow your online visibility. https://example.com" } | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri http://localhost:4000/posting -ContentType 'application/json' -Body $body `
  -Headers @{ 'Idempotency-Key' = 'docs-demo-001' } -UseBasicParsing | Select-Object StatusCode, Headers, Content
```

First call (**source: server**):

```http
HTTP 200
idempotent-replayed: false
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

Same key + same message again (**source: server**). Replayed from cache, nothing re-sent:

```http
HTTP 200
idempotent-replayed: true
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


## Validation errors (HTTP 422)

PowerShell 5.1 throws on non-2xx responses. Print the body with:

```powershell
try { Invoke-RestMethod -Method Post -Uri http://localhost:4000/posting -ContentType 'application/json' -Body '{"message": "No link here"}' }
catch { $_.ErrorDetails.Message }
```

All responses in this section: **source: server**.

### Empty after trimming

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "   "}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa https://example.com"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "No link here"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "See https://example.com and https://example.org"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "https://example.com and again https://example.com"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Visit www.example.com today"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Download ftp://x.com/file"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Click javascript:alert(1)"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Local https://localhost/admin"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Internal http://192.168.1.1/admin"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Login https://user:pw@example.com"}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Hi https://example.com", "platforms": ["mastodon"]}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": 42}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{}'
```

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

```bash
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" -d '{"message": '
```

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

## GET /health and GET /platforms

```bash
curl -s http://localhost:4000/health
curl -s http://localhost:4000/platforms
```

```powershell
Invoke-RestMethod http://localhost:4000/health
Invoke-RestMethod http://localhost:4000/platforms | ConvertTo-Json -Depth 4
```

**Source: server** (no credentials configured):

```http
HTTP 200
```

```json
{
  "status": "ok"
}
```

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
