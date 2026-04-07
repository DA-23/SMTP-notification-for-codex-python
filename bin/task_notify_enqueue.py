#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from task_notify_common import enqueue_event, hostname, isoformat_from_ts, now_ts, username  # noqa: E402


PYTHON_TOKEN_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def shell_split(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.strip().split()


def trim_leading_env_assignments(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens) and ENV_ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    return tokens[index:]


def is_python_token(token: str) -> bool:
    return bool(PYTHON_TOKEN_RE.fullmatch(Path(token).name))


def classify_python_command(command: str) -> str | None:
    tokens = trim_leading_env_assignments(shell_split(command))
    if not tokens:
        return None

    first = Path(tokens[0]).name
    if is_python_token(first):
        return first

    if first == "uv" and len(tokens) >= 3 and tokens[1] == "run" and is_python_token(tokens[2]):
        return f"uv run {Path(tokens[2]).name}"

    if first == "conda" and len(tokens) >= 3 and tokens[1] == "run":
        for token in tokens[2:]:
            if is_python_token(token):
                return f"conda run {Path(token).name}"

    return None


def build_shell_event(args: argparse.Namespace) -> dict[str, object] | None:
    runner = classify_python_command(args.command)
    if not runner:
        return None

    start_epoch = float(args.start_epoch)
    finish_epoch = now_ts()
    raw_key = f"{args.shell_session_id}:{args.shell_pid}:{start_epoch}:{args.command}:{args.cwd}"
    event_id = f"python-{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:24]}"
    duration = max(0.0, finish_epoch - start_epoch)

    return {
        "event_id": event_id,
        "kind": "python_task_finished",
        "created_at": isoformat_from_ts(finish_epoch),
        "started_at": isoformat_from_ts(start_epoch),
        "finished_at": isoformat_from_ts(finish_epoch),
        "duration_seconds": round(duration, 3),
        "exit_code": int(args.exit_code),
        "command": args.command,
        "runner": runner,
        "cwd": args.cwd,
        "shell_pid": int(args.shell_pid),
        "shell_session_id": args.shell_session_id,
        "hostname": hostname(),
        "user": username(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue task-notify events.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    shell_finish = subparsers.add_parser("shell-finish", help="Queue a Python shell completion event.")
    shell_finish.add_argument("--command", required=True)
    shell_finish.add_argument("--cwd", required=True)
    shell_finish.add_argument("--exit-code", required=True, type=int)
    shell_finish.add_argument("--start-epoch", required=True, type=float)
    shell_finish.add_argument("--shell-pid", required=True, type=int)
    shell_finish.add_argument("--shell-session-id", required=True)

    args = parser.parse_args()
    if args.subcommand != "shell-finish":
        return 1

    event = build_shell_event(args)
    if not event:
        return 0

    enqueue_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
