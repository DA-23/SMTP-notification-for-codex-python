#!/usr/bin/env python3
from __future__ import annotations

import getpass
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


HOME = Path.home()
BASE_DIR = HOME / ".local" / "share" / "task-notify"
CONFIG_PATH = HOME / ".config" / "task-notify" / "config.json"
ENV_PATH = HOME / ".config" / "task-notify" / "credentials.env"
LOG_DIR = HOME / "Library" / "Logs" / "task-notify"
SPOOL_DIR = BASE_DIR / "spool"
PENDING_DIR = SPOOL_DIR / "pending"
PROCESSING_DIR = SPOOL_DIR / "processing"
SENT_DIR = SPOOL_DIR / "sent"
DEAD_DIR = SPOOL_DIR / "dead"
LOG_PATH = LOG_DIR / "sender.log"


class RetryableSendError(Exception):
    pass


def now_ts() -> float:
    return time.time()


def isoformat_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def hostname() -> str:
    return socket.gethostname()


def username() -> str:
    return getpass.getuser()


def ensure_runtime_dirs() -> None:
    for path in (BASE_DIR, LOG_DIR, PENDING_DIR, PROCESSING_DIR, SENT_DIR, DEAD_DIR):
        path.mkdir(parents=True, exist_ok=True)


def log_line(message: str) -> None:
    ensure_runtime_dirs()
    line = f"{isoformat_from_ts(now_ts())} {message}\n"
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "smtp": {
            "host": "smtp.qq.com",
            "port": 465,
            "security": "ssl",
            "sender": "your_qq_number@qq.com",
            "recipient": "your_qq_number@qq.com",
            "keychain_service": "task-notify-qq-smtp",
            "auth_env_var": "TASK_NOTIFY_SMTP_PASSWORD",
        },
        "queue": {
            "poll_interval_seconds": 5,
            "base_retry_seconds": 15,
            "max_retry_seconds": 1800,
            "processing_stale_seconds": 600,
            "max_attempts": 0,
        },
        "email": {
            "subject_prefix": "[task-notify]",
        },
    }
    if not CONFIG_PATH.exists():
        return defaults
    file_config = read_json(CONFIG_PATH)
    return deep_merge(defaults, file_config)


def read_credentials_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_smtp_password(config: dict[str, Any]) -> str:
    smtp_cfg = config["smtp"]
    auth_env_var = smtp_cfg["auth_env_var"]
    password = os.environ.get(auth_env_var)
    if password:
        return password

    env_file_values = read_credentials_env()
    password = env_file_values.get(auth_env_var)
    if password:
        return password

    security_bin = shutil.which("security")
    if security_bin:
        account = smtp_cfg["sender"]
        service = smtp_cfg["keychain_service"]
        result = subprocess.run(
            [security_bin, "find-generic-password", "-a", account, "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            password = result.stdout.strip()
            if password:
                return password

    raise RetryableSendError(
        f"SMTP password is not configured. Set {auth_env_var}, populate {ENV_PATH}, "
        f"or add a Keychain item for service '{smtp_cfg['keychain_service']}'."
    )


def event_filename(event_id: str) -> str:
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"{digest}.json"


def event_path(directory: Path, event_id: str) -> Path:
    return directory / event_filename(event_id)


def existing_event_path(event_id: str) -> Path | None:
    filename = event_filename(event_id)
    for directory in (PENDING_DIR, PROCESSING_DIR, SENT_DIR, DEAD_DIR):
        candidate = directory / filename
        if candidate.exists():
            return candidate
    return None


def enqueue_event(event: dict[str, Any]) -> bool:
    ensure_runtime_dirs()
    event_id = str(event["event_id"])
    if existing_event_path(event_id):
        return False

    payload = {
        "event": event,
        "meta": {
            "attempts": 0,
            "created_at": isoformat_from_ts(now_ts()),
            "last_error": None,
            "next_attempt_after": None,
        },
    }
    atomic_write_json(event_path(PENDING_DIR, event_id), payload)
    return True


def recover_processing_files(config: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    stale_after = int(config["queue"]["processing_stale_seconds"])
    cutoff = now_ts() - stale_after
    for path in PROCESSING_DIR.glob("*.json"):
        if path.stat().st_mtime <= cutoff:
            payload = read_json(path)
            payload["meta"]["last_error"] = "Recovered stale processing file."
            payload["meta"]["next_attempt_after"] = isoformat_from_ts(now_ts())
            atomic_write_json(event_path(PENDING_DIR, payload["event"]["event_id"]), payload)
            path.unlink(missing_ok=True)


def next_retry_delay(config: dict[str, Any], attempts: int) -> int:
    base = int(config["queue"]["base_retry_seconds"])
    maximum = int(config["queue"]["max_retry_seconds"])
    return min(maximum, base * (2 ** max(0, attempts - 1)))


def should_retry(meta: dict[str, Any], config: dict[str, Any]) -> bool:
    max_attempts = int(config["queue"]["max_attempts"])
    if max_attempts <= 0:
        return True
    return int(meta["attempts"]) < max_attempts


def next_attempt_due(meta: dict[str, Any]) -> bool:
    deadline = meta.get("next_attempt_after")
    if not deadline:
        return True
    try:
        retry_at = datetime.fromisoformat(deadline).timestamp()
    except ValueError:
        return True
    return retry_at <= now_ts()


def build_subject(event: dict[str, Any], config: dict[str, Any]) -> str:
    prefix = config["email"]["subject_prefix"]
    if event["kind"] == "python_task_finished":
        command = event["command"].strip().replace("\n", " ")
        preview = (command[:72] + "...") if len(command) > 75 else command
        return f"{prefix} [python exit={event['exit_code']}] {preview}"
    if event["kind"] == "codex_turn_completed":
        project = event.get("cwd") or "~"
        return f"{prefix} [codex] turn completed in {project}"
    return f"{prefix} event {event['event_id']}"


def build_body(event: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"event_id: {event['event_id']}")
    lines.append(f"kind: {event['kind']}")
    lines.append(f"user: {event['user']}")
    lines.append(f"host: {event['hostname']}")
    lines.append(f"cwd: {event.get('cwd', '')}")
    lines.append(f"created_at: {event['created_at']}")

    if event["kind"] == "python_task_finished":
        lines.append(f"exit_code: {event['exit_code']}")
        lines.append(f"duration_seconds: {event['duration_seconds']}")
        lines.append(f"started_at: {event['started_at']}")
        lines.append(f"finished_at: {event['finished_at']}")
        lines.append(f"shell_pid: {event['shell_pid']}")
        lines.append(f"shell_session_id: {event['shell_session_id']}")
        lines.append(f"runner: {event['runner']}")
        lines.append("")
        lines.append("command:")
        lines.append(event["command"])
        return "\n".join(lines) + "\n"

    if event["kind"] == "codex_turn_completed":
        lines.append(f"turn_id: {event['turn_id']}")
        lines.append(f"stop_hook_active: {event['stop_hook_active']}")
        lines.append("")
        lines.append("assistant_message:")
        lines.append(event["assistant_message"])
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True))
    return "\n".join(lines) + "\n"


@dataclass
class PreparedMessage:
    subject: str
    body: str
    message_id: str


def prepare_email(event: dict[str, Any], config: dict[str, Any]) -> PreparedMessage:
    subject = build_subject(event, config)
    body = build_body(event)
    fqdn = socket.getfqdn() or hostname()
    message_id = f"<{event['event_id']}@{fqdn}>"
    return PreparedMessage(subject=subject, body=body, message_id=message_id)


def format_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)
