"""Live run + evidence capture. Publishes for real: use test accounts only.

Usage (server already running, ideally with LOG_FILE set in .env):
    python scripts/live_run.py --label live_success
    python scripts/live_run.py --label partial_failure --base-url http://localhost:4000

It POSTs the sample message (CTA link https://example.com) to /posting with a fresh X-Request-ID, then writes:
    docs/evidence/<label>_<UTC timestamp>.json   the response body exactly as received (never edited)
    docs/evidence/<label>_<UTC timestamp>.log    the server's log lines for that request id (redacted again)
and prints each platform's status and posted_url.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core.logging import redact, register_secrets  # noqa: E402

SAMPLE_MESSAGE = "Check out our latest business platform and grow your online visibility. https://example.com"
EVIDENCE_DIR = ROOT / "docs" / "evidence"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def log_lines_for_request(log_file: Path, request_id: str) -> list[str]:
    """Return the JSON log lines whose request_id matches, redacted again before saving."""
    if not log_file.is_file():
        return []
    lines = []
    for raw in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("request_id") == request_id:
            lines.append(redact(raw))
    return lines


async def live_run(
    label: str,
    base_url: str,
    *,
    log_file: Path | None,
    out_dir: Path = EVIDENCE_DIR,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, Path, Path | None]:
    """Run once. Returns (http_status, json_path, log_path_or_None)."""
    if not _LABEL_RE.fullmatch(label):
        raise ValueError("--label must be 1-60 characters of letters, digits, '_' or '-'")
    request_id = f"live-{uuid.uuid4().hex[:16]}"
    stamp = utc_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{label}_{stamp}.json"

    async with httpx.AsyncClient(base_url=base_url, transport=transport, timeout=120) as client:
        response = await client.post("/posting", json={"message": SAMPLE_MESSAGE}, headers={"X-Request-ID": request_id})

    # Save exactly what came back. Pretty-print valid JSON for readability; never alter values.
    try:
        body = response.json()
        json_path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except ValueError:
        body = None
        json_path.write_text(response.text, encoding="utf-8")

    log_path: Path | None = None
    if log_file is not None:
        lines = log_lines_for_request(log_file, request_id)
        if lines:
            log_path = json_path.with_suffix(".log")
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"HTTP {response.status_code}  request_id={request_id}")
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        print(f"summary: {body.get('summary')}")
        for item in body["results"]:
            detail = item.get("posted_url") if item.get("status") == "success" else item.get("error")
            name, state, public = item.get("platform", "?"), item.get("status", "?"), item.get("public_access")
            print(f"  {name:<12} {state:<8} public_access={public}  {detail}")
    print(f"saved: {json_path.relative_to(ROOT) if json_path.is_relative_to(ROOT) else json_path}")
    if log_path:
        print(f"saved: {log_path.relative_to(ROOT) if log_path.is_relative_to(ROOT) else log_path}")
    elif log_file is None:
        print("note: LOG_FILE is not set, so no server log lines were captured (set LOG_FILE in .env and restart).")
    else:
        print(f"note: no log lines for {request_id} found in {log_file} (is the server writing to that LOG_FILE?)")
    return response.status_code, json_path, log_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="POST the sample message to /posting and save the evidence.")
    parser.add_argument("--label", required=True, help="evidence file prefix, e.g. live_success or partial_failure")
    parser.add_argument("--base-url", default="http://localhost:4000")
    parser.add_argument("--log-file", help="server LOG_FILE to read (default: LOG_FILE from .env)")
    args = parser.parse_args(argv)

    settings = Settings()
    register_secrets(settings.secret_values())
    log_value = args.log_file or settings.log_file
    log_file = (ROOT / log_value if not Path(log_value).is_absolute() else Path(log_value)) if log_value else None
    try:
        status, _, _ = asyncio.run(live_run(args.label, args.base_url, log_file=log_file))
    except httpx.HTTPError as exc:
        print(f"Could not reach {args.base_url} ({type(exc).__name__}). Is the server running?")
        return 2
    except ValueError as exc:
        print(str(exc))
        return 2
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
