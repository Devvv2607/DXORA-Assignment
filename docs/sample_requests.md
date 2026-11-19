# Sample requests

All examples assume the server is running locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

> Live runs publish real posts. Use dedicated test accounts and only `https://example.com` as the CTA link.

## Successful post (all enabled platforms)

```bash
curl -s -X POST http://localhost:4000/posting \
  -H "Content-Type: application/json" \
  -d '{"message": "Check out our latest business platform and grow your online visibility. https://example.com"}'
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:4000/posting -ContentType 'application/json' `
  -Body '{"message": "Check out our latest business platform and grow your online visibility. https://example.com"}' |
  ConvertTo-Json -Depth 5
```

## With an idempotency key (safe client retries)

```bash
curl -s -i -X POST http://localhost:4000/posting \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: launch-2026-11-18-001" \
  -d '{"message": "Check out our latest business platform and grow your online visibility. https://example.com"}'
```

Sending the same request again within 10 minutes returns the same body with `Idempotent-Replayed: true`
and posts nothing. The same key with a different message returns 422 `idempotency_conflict`.

## Partial-failure demo (one wrong token)

1. In `.env`, set `DEVTO_API_KEY=deliberately-wrong`.
2. Restart the server and send the success request above.
3. Dev.to shows `AUTH_FAILED`. Every other platform still publishes, and the response is HTTP 200 with `"success": true`.

## Validation errors (HTTP 422)

```bash
# too long (more than 200 characters after trimming)
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa https://example.com"}'

# no link
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "No link here"}'

# two links
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "See https://example.com and https://example.org"}'

# private / local address
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Internal https://192.168.1.1/admin"}'

# client tries to choose platforms (unknown field)
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": "Hi https://example.com", "platforms": ["mastodon"]}'

# malformed JSON
curl -s -X POST http://localhost:4000/posting -H "Content-Type: application/json" \
  -d '{"message": '
```

## Introspection

```bash
curl -s http://localhost:4000/health
curl -s http://localhost:4000/platforms
```

## Safe local run (no posts are made)

```bash
DRY_RUN=true uvicorn app.main:app --host 0.0.0.0 --port 4000      # bash
$env:DRY_RUN="true"; uvicorn app.main:app --host 0.0.0.0 --port 4000  # PowerShell
```
