# Live-run evidence

This folder holds **only** output from real live runs. Mocked and DRY_RUN samples live in
[`../sample_responses.md`](../sample_responses.md).

Produced by `scripts/live_run.py` (see the main README, "Helper scripts"):

- `live_success_<UTC timestamp>.json`: the full `POST /posting` response, exactly as received.
- `live_success_<UTC timestamp>.log`: the server's redacted log lines for that request id (requires `LOG_FILE`).
- `partial_failure_<UTC timestamp>.json` / `.log`: the same, with one deliberately wrong token.
- `screenshots/`: one screenshot per `posted_url`, opened in a private/incognito window (this proves public access).

The README's Verification status table may mark a platform "verified live" only if a JSON here shows
`"status": "success"` for it, with `public_access` copied exactly.

Raw captures that could contain account identifiers should use the `.raw` extension, which is git-ignored.
Never store tokens here. Run `python scripts/check_no_secrets.py` before committing.
