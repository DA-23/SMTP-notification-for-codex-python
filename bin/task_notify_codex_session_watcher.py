#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from task_notify_common import (  # noqa: E402
    BASE_DIR,
    SingleInstanceLock,
    atomic_write_json,
    enqueue_event,
    ensure_runtime_dirs,
    hostname,
    isoformat_from_ts,
    log_line,
    now_ts,
    read_json,
    username,
)


DEFAULT_SESSION_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_STATE_PATH = BASE_DIR / "state" / "codex_session_watcher_state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Codex session JSONL files and enqueue notifications without native hooks."
    )
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=3.0,
        help="Polling interval when running continuously.",
    )
    parser.add_argument(
        "--session-root",
        type=Path,
        default=DEFAULT_SESSION_ROOT,
        help="Root directory containing Codex session JSONL files.",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Path used to persist watcher offsets.",
    )
    return parser.parse_args()


def default_state() -> dict[str, Any]:
    return {
        "initialized_at": isoformat_from_ts(now_ts()),
        "files": {},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()

    try:
        state = read_json(path)
    except Exception as exc:
        log_line(f"codex_watcher invalid state file {path}: {exc}")
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            path.replace(backup)
        except OSError:
            pass
        return default_state()

    if not isinstance(state, dict):
        return default_state()

    files = state.get("files")
    if not isinstance(files, dict):
        files = {}

    initialized_at = state.get("initialized_at")
    if not isinstance(initialized_at, str) or not initialized_at.strip():
        initialized_at = isoformat_from_ts(now_ts())

    return {
        "initialized_at": initialized_at,
        "files": files,
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state)


def read_session_meta(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(32):
                line = handle.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "session_meta":
                    continue
                payload = record.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                return {
                    "cwd": str(payload.get("cwd") or ""),
                    "originator": str(payload.get("originator") or ""),
                    "cli_version": str(payload.get("cli_version") or ""),
                    "source": str(payload.get("source") or ""),
                }
    except OSError as exc:
        log_line(f"codex_watcher failed to read session meta {path}: {exc}")
    return {}


def ensure_file_state(
    path: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    key = str(path)
    files = state["files"]
    file_state = files.get(key)
    if isinstance(file_state, dict):
        if not isinstance(file_state.get("session"), dict):
            file_state["session"] = read_session_meta(path)
        if not isinstance(file_state.get("offset"), int):
            file_state["offset"] = 0
        return file_state

    session = read_session_meta(path)
    file_state = {
        "offset": 0,
        "session": session,
    }
    files[key] = file_state
    return file_state


def seed_existing_files(session_root: Path, state: dict[str, Any]) -> None:
    if not session_root.exists():
        return

    for path in sorted(session_root.rglob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(0, os.SEEK_END)
                offset = handle.tell()
        except OSError as exc:
            log_line(f"codex_watcher failed to seed {path}: {exc}")
            continue

        state["files"][str(path)] = {
            "offset": offset,
            "session": read_session_meta(path),
        }


def build_event(record: dict[str, Any], file_state: dict[str, Any]) -> dict[str, Any] | None:
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    message = payload.get("last_agent_message")
    if not isinstance(message, str) or not message.strip():
        return None

    turn_id = str(payload.get("turn_id") or "").strip()
    if not turn_id:
        return None

    session = file_state.get("session") or {}
    if not isinstance(session, dict):
        session = {}

    created_at = record.get("timestamp")
    if not isinstance(created_at, str) or not created_at.strip():
        created_at = isoformat_from_ts(now_ts())

    return {
        "event_id": f"codex-{turn_id}",
        "kind": "codex_turn_completed",
        "created_at": created_at,
        "turn_id": turn_id,
        "stop_hook_active": False,
        "assistant_message": message,
        "cwd": str(session.get("cwd") or ""),
        "hostname": hostname(),
        "user": username(),
        "source": "session_watcher",
        "codex_originator": str(session.get("originator") or ""),
        "codex_cli_version": str(session.get("cli_version") or ""),
        "codex_source": str(session.get("source") or ""),
    }


def process_record(record: dict[str, Any], file_state: dict[str, Any]) -> int:
    record_type = record.get("type")
    if record_type == "session_meta":
        payload = record.get("payload") or {}
        if isinstance(payload, dict):
            file_state["session"] = {
                "cwd": str(payload.get("cwd") or ""),
                "originator": str(payload.get("originator") or ""),
                "cli_version": str(payload.get("cli_version") or ""),
                "source": str(payload.get("source") or ""),
            }
        return 0

    if record_type != "event_msg":
        return 0

    payload = record.get("payload") or {}
    if not isinstance(payload, dict) or payload.get("type") != "task_complete":
        return 0

    event = build_event(record, file_state)
    if not event:
        return 0

    if enqueue_event(event):
        log_line(
            "codex_watcher enqueued "
            f"{event['event_id']} originator={event['codex_originator'] or 'unknown'}"
        )
        return 1
    return 0


def process_file(path: Path, file_state: dict[str, Any]) -> int:
    enqueued = 0
    offset = int(file_state.get("offset") or 0)
    try:
        with path.open("r", encoding="utf-8") as handle:
            try:
                handle.seek(offset)
            except OSError:
                handle.seek(0)
                offset = 0

            while True:
                start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    handle.seek(start)
                    break

                offset = handle.tell()
                stripped = line.strip()
                if not stripped:
                    continue

                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    log_line(f"codex_watcher invalid json in {path}: {exc}")
                    continue

                enqueued += process_record(record, file_state)
    except OSError as exc:
        log_line(f"codex_watcher failed to process {path}: {exc}")
        return 0

    file_state["offset"] = offset
    return enqueued


def scan_once(session_root: Path, state: dict[str, Any]) -> int:
    if not session_root.exists():
        return 0

    paths = sorted(session_root.rglob("*.jsonl"))
    live_keys = set()
    enqueued = 0

    for path in paths:
        live_keys.add(str(path))
        file_state = ensure_file_state(path, state)
        enqueued += process_file(path, file_state)

    stale_keys = [key for key in state["files"] if key not in live_keys]
    for key in stale_keys:
        state["files"].pop(key, None)

    return enqueued


def run_loop(session_root: Path, state_path: Path, poll_seconds: float) -> int:
    ensure_runtime_dirs()
    lock = SingleInstanceLock(BASE_DIR / "state" / "codex_session_watcher.lock")
    if not lock.acquire():
        log_line("codex_watcher loop skipped: another watcher instance is already running")
        return 0
    log_line(f"codex_watcher loop started root={session_root}")
    state_exists = state_path.exists()
    state = load_state(state_path)
    if not state_exists:
        seed_existing_files(session_root, state)
        save_state(state_path, state)
        log_line("codex_watcher seeded existing sessions without replay")

    try:
        while True:
            try:
                enqueued = scan_once(session_root, state)
                save_state(state_path, state)
                if enqueued:
                    log_line(f"codex_watcher scan enqueued={enqueued}")
            except KeyboardInterrupt:
                log_line("codex_watcher interrupted")
                return 0
            except Exception as exc:
                log_line(f"codex_watcher loop error: {exc}")
            time.sleep(max(0.5, poll_seconds))
    finally:
        lock.release()


def main() -> int:
    args = parse_args()
    session_root = args.session_root.expanduser()
    state_path = args.state_path.expanduser()

    if args.once:
        ensure_runtime_dirs()
        state_exists = state_path.exists()
        state = load_state(state_path)
        if not state_exists:
            seed_existing_files(session_root, state)
            save_state(state_path, state)
            log_line("codex_watcher seeded existing sessions without replay")
            return 0
        enqueued = scan_once(session_root, state)
        save_state(state_path, state)
        if enqueued:
            log_line(f"codex_watcher single scan enqueued={enqueued}")
        return 0

    return run_loop(session_root, state_path, args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
