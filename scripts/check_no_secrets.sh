#!/usr/bin/env bash
# Thin wrapper: the real scanner is scripts/check_no_secrets.py (cross-platform, used by CI).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" scripts/check_no_secrets.py "$@"
