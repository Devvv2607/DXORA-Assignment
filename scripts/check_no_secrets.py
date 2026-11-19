"""Cross-platform secret scan. Exit code 1 if anything credential-like would be committed.

Usage:  python scripts/check_no_secrets.py [--root PATH]

Scans tracked + untracked-but-not-ignored files (via git), or the whole tree minus the usual ignored
paths when git is unavailable. ``.env.example`` IS scanned: its placeholders must stay empty, so a template
filled in by mistake fails the scan. Prints ``file:line`` only, never the match.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Telegram bot token", re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("GitHub fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Discord webhook URL", re.compile(r"discord(?:app)?\.com/api/webhooks/[0-9]{17,20}/[A-Za-z0-9_-]{50,}")),
    ("Bluesky app password", re.compile(r"\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("hard-coded bearer token", re.compile(r"Bearer [A-Za-z0-9._~+/-]{30,}")),
    (
        "filled-in credential variable",
        # Any non-empty value for a secret variable (also `KEY= value`, `KEY="value"`, `export KEY=value`).
        # `KEY=`, `KEY=""` and `KEY=  # comment` pass, which is what .env.example must look like.
        re.compile(
            r"^[ \t]*(?:export[ \t]+)?(?:MASTODON_ACCESS_TOKEN|BLUESKY_APP_PASSWORD|TELEGRAM_BOT_TOKEN|DEVTO_API_KEY|"
            r"GITHUB_TOKEN|WP_APP_PASSWORD|LINKEDIN_ACCESS_TOKEN|DISCORD_WEBHOOK_URL|API_KEY)=[ \t]*[\"']?[^\s#\"']",
            re.MULTILINE,
        ),
    ),
]

EXCLUDED_FILES = {"scripts/check_no_secrets.py"}  # .env.example is scanned on purpose
_WALK_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "logs", "node_modules"}
_MAX_BYTES = 2_000_000


def list_files(root: Path) -> list[str]:
    """Files that git would commit; falls back to a filtered directory walk without git."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "ls-files", "-co", "--exclude-standard"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS]
            for name in filenames:
                if name == ".env" or name.endswith(".raw"):
                    continue
                files.append(Path(dirpath, name).relative_to(root).as_posix())
        return files


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line_number, pattern_name) for every match."""
    findings = []
    for name, pattern in PATTERNS:
        for match in pattern.finditer(text):
            findings.append((text.count("\n", 0, match.start()) + 1, name))
    return sorted(set(findings))


def scan(root: Path) -> list[tuple[str, int, str]]:
    findings = []
    for rel in list_files(root):
        if rel in EXCLUDED_FILES:
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > _MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:  # binary file
            continue
        for line, name in scan_text(data.decode("utf-8", errors="replace")):
            findings.append((rel, line, name))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if credential-like strings would be committed.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    root = Path(parser.parse_args(argv).root).resolve()
    findings = scan(root)
    for rel, line, name in findings:
        print(f"POSSIBLE SECRET: {rel}:{line} ({name})")
    if findings:
        print("Secret scan FAILED. Remove the credentials above (and rotate them if they were real).", file=sys.stderr)
        return 1
    print(f"Secret scan passed: no credential-like strings in {len(list_files(root))} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
