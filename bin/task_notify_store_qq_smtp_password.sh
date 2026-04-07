#!/bin/zsh
set -euo pipefail

sender_email="${1:-}"

if ! command -v security >/dev/null 2>&1; then
  echo "security command not found" >&2
  exit 1
fi

if [[ -z "${sender_email}" && -f "$HOME/.config/task-notify/config.json" ]]; then
  sender_email="$(python3 - <<'PY'
import json
from pathlib import Path
path = Path.home() / ".config" / "task-notify" / "config.json"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(data.get("smtp", {}).get("sender", ""))
except Exception:
    print("")
PY
)"
fi

if [[ -z "${sender_email}" ]]; then
  echo "Usage: $0 your_sender_email@example.com" >&2
  exit 1
fi

read -r -s "smtp_password?QQ SMTP auth code: "
echo

if [[ -z "${smtp_password}" ]]; then
  echo "No auth code entered" >&2
  exit 1
fi

security add-generic-password \
  -U \
  -a "${sender_email}" \
  -s task-notify-qq-smtp \
  -w "${smtp_password}"

echo "Stored SMTP auth code in Keychain service task-notify-qq-smtp for ${sender_email}"
