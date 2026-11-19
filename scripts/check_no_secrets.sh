#!/usr/bin/env bash
# Fail if anything that looks like a real credential is present in files that would be committed.
# Scans tracked + untracked-but-not-ignored files (git), or the working tree minus ignored paths (no git).
set -euo pipefail
cd "$(dirname "$0")/.."

PATTERNS=(
  '[0-9]{8,10}:[A-Za-z0-9_-]{35}'                                   # Telegram bot token
  'gh[pousr]_[A-Za-z0-9]{36}'                                       # GitHub classic/OAuth tokens
  'github_pat_[A-Za-z0-9_]{22,}'                                    # GitHub fine-grained token
  'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'   # JWT (e.g. Bluesky accessJwt)
  'discord(app)?\.com/api/webhooks/[0-9]{17,20}/[A-Za-z0-9_-]{50,}' # Discord webhook URL
  '\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b'             # Bluesky app password
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'                              # private keys
  'Bearer [A-Za-z0-9._~+/-]{30,}'                                   # hard-coded bearer token
  '^[[:space:]]*(MASTODON_ACCESS_TOKEN|BLUESKY_APP_PASSWORD|TELEGRAM_BOT_TOKEN|DEVTO_API_KEY|GITHUB_TOKEN|WP_APP_PASSWORD|LINKEDIN_ACCESS_TOKEN|DISCORD_WEBHOOK_URL|API_KEY)=[^[:space:]#]+'  # filled-in env file
)

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files -co --exclude-standard)
else
  mapfile -t FILES < <(find . -type f \
    -not -path './.git/*' -not -path './.venv/*' -not -path './venv/*' \
    -not -path '*/__pycache__/*' -not -path './.pytest_cache/*' -not -path './.ruff_cache/*' \
    -not -name '.env' -not -name '*.raw' | sed 's|^\./||')
fi

found=0
for file in "${FILES[@]}"; do
  case "$file" in
    .env.example|scripts/check_no_secrets.sh) continue ;;
  esac
  [ -f "$file" ] || continue
  for pattern in "${PATTERNS[@]}"; do
    if grep -EnI -e "$pattern" "$file" >/dev/null 2>&1; then
      # Print file:line only, never the matching text itself.
      grep -EnI -e "$pattern" "$file" | cut -d: -f1 | sed "s|^|POSSIBLE SECRET: $file:|"
      found=1
    fi
  done
done

if [ "$found" -ne 0 ]; then
  echo "Secret scan FAILED. Remove the credentials above (and rotate them if they were real)." >&2
  exit 1
fi
echo "Secret scan passed: no credential-like strings in ${#FILES[@]} files."
