# Live-run evidence

Put real proof of live runs here:

- `live_success.json`: the full `POST /posting` response from a live run (all 5 core platforms).
- `partial_failure.json`: the response with one deliberately wrong token.
- `screenshots/`: one screenshot per `posted_url`, opened in a private/incognito window (this proves public access).

Raw captures that could contain account identifiers should use the `.raw` extension, which is git-ignored.
Never store tokens here. Run `bash scripts/check_no_secrets.sh` before committing.
