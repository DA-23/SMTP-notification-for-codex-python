#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from task_notify_common import enqueue_event, hostname, isoformat_from_ts, log_line, now_ts, username  # noqa: E402


def emit_empty_stop_response() -> None:
    sys.stdout.write("{}\n")
    sys.stdout.flush()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # pragma: no cover - must fail open for Codex
        log_line(f"codex_stop invalid input: {exc}")
        emit_empty_stop_response()
        return 0

    try:
        message = payload.get("last_assistant_message")
        if not isinstance(message, str) or not message.strip():
            emit_empty_stop_response()
            return 0

        turn_id = str(payload.get("turn_id") or "")
        if not turn_id:
            raw = f"{now_ts()}:{message}"
            turn_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

        event = {
            "event_id": f"codex-{turn_id}",
            "kind": "codex_turn_completed",
            "created_at": isoformat_from_ts(now_ts()),
            "turn_id": turn_id,
            "stop_hook_active": bool(payload.get("stop_hook_active", False)),
            "assistant_message": message,
            "cwd": os.getcwd(),
            "hostname": hostname(),
            "user": username(),
        }
        enqueue_event(event)
    except Exception as exc:  # pragma: no cover - must fail open for Codex
        log_line(f"codex_stop enqueue failed: {exc}")

    emit_empty_stop_response()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
