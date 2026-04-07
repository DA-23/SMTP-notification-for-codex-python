#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from task_notify_common import (  # noqa: E402
    DEAD_DIR,
    PENDING_DIR,
    PROCESSING_DIR,
    SENT_DIR,
    RetryableSendError,
    atomic_write_json,
    build_body,
    ensure_runtime_dirs,
    event_path,
    format_bool,
    load_config,
    load_smtp_password,
    log_line,
    next_attempt_due,
    next_retry_delay,
    prepare_email,
    read_json,
    recover_processing_files,
    should_retry,
)


def smtp_client(config: dict[str, object]) -> smtplib.SMTP:
    smtp_cfg = config["smtp"]
    host = smtp_cfg["host"]
    port = int(smtp_cfg["port"])
    security = str(smtp_cfg["security"]).lower()
    if security == "ssl":
        context = ssl.create_default_context()
        return smtplib.SMTP_SSL(host=host, port=port, timeout=30, context=context)
    client = smtplib.SMTP(host=host, port=port, timeout=30)
    client.ehlo()
    if security == "starttls":
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def send_event_email(config: dict[str, object], event: dict[str, object], dry_run: bool) -> str:
    prepared = prepare_email(event, config)
    smtp_cfg = config["smtp"]

    if dry_run:
        log_line(f"dry-run accepted {event['event_id']} {prepared.subject}")
        return "dry-run"

    password = load_smtp_password(config)
    sender = smtp_cfg["sender"]
    recipient = smtp_cfg["recipient"]

    message = EmailMessage()
    message["Subject"] = prepared.subject
    message["From"] = sender
    message["To"] = recipient
    message["Message-ID"] = prepared.message_id
    message.set_content(prepared.body)

    with smtp_client(config) as client:
        client.ehlo()
        auth_features = str(client.esmtp_features.get("auth", ""))
        if "LOGIN" in auth_features.upper():
            client.esmtp_features["auth"] = "LOGIN"
        client.login(sender, password)
        refused = client.send_message(message)
        if refused:
            raise RetryableSendError(f"SMTP refused recipients: {json.dumps(refused, ensure_ascii=False)}")
        return "smtp-accepted"


def claim_event_file(path: Path) -> Path | None:
    processing_path = PROCESSING_DIR / path.name
    try:
        path.replace(processing_path)
        return processing_path
    except FileNotFoundError:
        return None


def handle_retry(processing_path: Path, payload: dict[str, object], config: dict[str, object], error: Exception) -> None:
    meta = payload["meta"]
    meta["attempts"] = int(meta["attempts"]) + 1
    meta["last_error"] = str(error)
    if should_retry(meta, config):
        delay = next_retry_delay(config, int(meta["attempts"]))
        meta["next_attempt_after"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z",
            time.localtime(time.time() + delay),
        )
        atomic_write_json(PENDING_DIR / processing_path.name, payload)
        processing_path.unlink(missing_ok=True)
        log_line(f"retry scheduled for {payload['event']['event_id']} in {delay}s: {error}")
        return

    atomic_write_json(DEAD_DIR / processing_path.name, payload)
    processing_path.unlink(missing_ok=True)
    log_line(f"dead-lettered {payload['event']['event_id']}: {error}")


def mark_sent(processing_path: Path, payload: dict[str, object], outcome: str) -> None:
    payload["meta"]["next_attempt_after"] = None
    payload["meta"]["last_error"] = None
    payload["meta"]["accepted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    payload["meta"]["outcome"] = outcome
    atomic_write_json(SENT_DIR / processing_path.name, payload)
    processing_path.unlink(missing_ok=True)
    log_line(f"accepted {payload['event']['event_id']} ({outcome})")


def process_one(config: dict[str, object], dry_run: bool) -> bool:
    ensure_runtime_dirs()
    for pending_path in sorted(PENDING_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime):
        payload = read_json(pending_path)
        if not next_attempt_due(payload["meta"]):
            continue
        processing_path = claim_event_file(pending_path)
        if not processing_path:
            continue
        payload = read_json(processing_path)
        try:
            outcome = send_event_email(config, payload["event"], dry_run=dry_run)
        except Exception as exc:
            handle_retry(processing_path, payload, config, exc)
            return True
        mark_sent(processing_path, payload, outcome)
        return True
    return False


def run_forever(config: dict[str, object], dry_run: bool) -> int:
    recover_processing_files(config)
    idle_sleep = int(config["queue"]["poll_interval_seconds"])
    log_line("sender loop started")
    while True:
        try:
            handled = process_one(config, dry_run=dry_run)
        except Exception as exc:
            log_line(f"sender loop error: {exc}")
            handled = False
        if not handled:
            time.sleep(idle_sleep)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send queued task-notify emails.")
    parser.add_argument("--once", action="store_true", help="Process at most one eligible event and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Mark events as accepted without SMTP.")
    args = parser.parse_args()

    config = load_config()
    recover_processing_files(config)

    if args.once:
        process_one(config, dry_run=args.dry_run)
        return 0

    return run_forever(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
