#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
SYSTEM = platform.system()
IS_DARWIN = SYSTEM == "Darwin"
IS_WINDOWS = SYSTEM == "Windows"

INSTALL_BASE = HOME / ".local" / "share" / "task-notify"
INSTALL_BIN = INSTALL_BASE / "bin"
INSTALL_LIB = INSTALL_BASE / "lib"
CONFIG_DIR = HOME / ".config" / "task-notify"
CODEX_DIR = HOME / ".codex"
LOG_DIR = HOME / "Library" / "Logs" / "task-notify"
LAUNCH_AGENTS_DIR = HOME / "Library" / "LaunchAgents"
WINDOWS_STARTUP_DIR = HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

ZSH_INCLUDE = CONFIG_DIR / "task-notify.zsh"
ZSHRC = HOME / ".zshrc"
CODEX_CONFIG = CODEX_DIR / "config.toml"
CODEX_HOOKS = CODEX_DIR / "hooks.json"
PLIST_PATH = LAUNCH_AGENTS_DIR / "com.task-notify.sender.plist"
WINDOWS_TASK_NAME = "TaskNotifySender"
WINDOWS_WATCHER_TASK_NAME = "TaskNotifyCodexWatcher"
WINDOWS_STARTUP_SCRIPT = WINDOWS_STARTUP_DIR / "task-notify-sender.cmd"
WINDOWS_WATCHER_STARTUP_SCRIPT = WINDOWS_STARTUP_DIR / "task-notify-codex-watcher.cmd"

ZSH_SOURCE_LINE = 'if [ -f "$HOME/.config/task-notify/task-notify.zsh" ]; then\n    . "$HOME/.config/task-notify/task-notify.zsh"\nfi\n'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_tree_contents(src: Path, dst: Path) -> None:
    ensure_dir(dst)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            continue
        shutil.copy2(item, target)
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IXUSR)


def merge_codex_hooks(rendered_command: str) -> None:
    ensure_dir(CODEX_DIR)
    desired_entry = {
        "type": "command",
        "command": rendered_command,
        "timeout": 30,
    }

    existing: dict[str, object] = {}
    if CODEX_HOOKS.exists():
        try:
            existing = json.loads(CODEX_HOOKS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = CODEX_HOOKS.with_suffix(".json.bak")
            shutil.copy2(CODEX_HOOKS, backup)

    hooks = existing.setdefault("hooks", {})
    stop_entries = hooks.setdefault("Stop", [])
    if not isinstance(stop_entries, list):
        stop_entries = []
        hooks["Stop"] = stop_entries

    for group in stop_entries:
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            continue
        if any(isinstance(h, dict) and h.get("command") == rendered_command for h in group_hooks):
            break
    else:
        stop_entries.append({"hooks": [desired_entry]})

    CODEX_HOOKS.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_codex_hooks_feature() -> None:
    ensure_dir(CODEX_DIR)
    if CODEX_CONFIG.exists():
        text = CODEX_CONFIG.read_text(encoding="utf-8")
    else:
        text = ""

    if "[features]" not in text:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n[features]\ncodex_hooks = true\n"
    elif "codex_hooks" not in text.split("[features]", 1)[1]:
        text = text.replace("[features]\n", "[features]\ncodex_hooks = true\n", 1)
    else:
        lines = []
        in_features = False
        replaced = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_features = stripped == "[features]"
            if in_features and stripped.startswith("codex_hooks"):
                lines.append("codex_hooks = true")
                replaced = True
                continue
            lines.append(line)
        if replaced:
            text = "\n".join(lines) + "\n"
        else:
            text = "\n".join(lines)
    CODEX_CONFIG.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def ensure_zsh_source() -> None:
    if ZSHRC.exists():
        text = ZSHRC.read_text(encoding="utf-8")
    else:
        text = ""
    if 'task-notify/task-notify.zsh' in text:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + ZSH_SOURCE_LINE
    ZSHRC.write_text(text, encoding="utf-8")


def render_plist(python_executable: str) -> str:
    sender = INSTALL_BIN / "task_notify_sender.py"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.task-notify.sender</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_executable}</string>
    <string>{sender}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>{HOME}</string>
  <key>StandardOutPath</key>
  <string>{LOG_DIR / 'launchd.stdout.log'}</string>
  <key>StandardErrorPath</key>
  <string>{LOG_DIR / 'launchd.stderr.log'}</string>
</dict>
</plist>
"""


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sender_running_windows() -> bool:
    script = (
        "(Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -like '*task_notify_sender.py*' }).Count"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "0"}


def watcher_running_windows() -> bool:
    script = (
        "(Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^pythonw?\\.exe$' "
        "-and $_.CommandLine -like '*task_notify_codex_session_watcher.py*' }).Count"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "0"}


def write_default_config() -> None:
    target = CONFIG_DIR / "config.json"
    if not target.exists():
        shutil.copy2(REPO_ROOT / "config" / "config.json", target)
    shutil.copy2(REPO_ROOT / "config" / "credentials.env.example", CONFIG_DIR / "credentials.env.example")
    shutil.copy2(REPO_ROOT / "config" / "task-notify.zsh", ZSH_INCLUDE)


def bootstrap_launch_agent() -> None:
    if not IS_DARWIN:
        return
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST_PATH)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)], check=False, capture_output=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/com.task-notify.sender"], check=False, capture_output=True)


def select_background_python(python_executable: str) -> str:
    if not IS_WINDOWS:
        return python_executable
    candidate = Path(python_executable).with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return python_executable


def bootstrap_windows_task(background_python: str) -> None:
    if not IS_WINDOWS:
        return

    sender = INSTALL_BIN / "task_notify_sender.py"
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$action = New-ScheduledTaskAction -Execute {powershell_literal(background_python)} "
            f"-Argument {powershell_literal(str(sender))} -WorkingDirectory {powershell_literal(str(HOME))}",
            "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
            "$settings = New-ScheduledTaskSettingsSet "
            "-AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries "
            "-StartWhenAvailable "
            "-MultipleInstances IgnoreNew",
            "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
            f"Register-ScheduledTask -TaskName {powershell_literal(WINDOWS_TASK_NAME)} "
            "-Action $action -Trigger $trigger -Settings $settings -Principal $principal "
            "-Description 'Run task-notify SMTP sender at logon' -Force | Out-Null",
            f"Start-ScheduledTask -TaskName {powershell_literal(WINDOWS_TASK_NAME)}",
        ]
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("Warning: failed to register Windows scheduled task for task-notify; falling back to Startup folder.")
        if result.stderr.strip():
            print(result.stderr.strip())


def bootstrap_windows_watcher_task(background_python: str) -> None:
    if not IS_WINDOWS:
        return

    watcher = INSTALL_BIN / "task_notify_codex_session_watcher.py"
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$action = New-ScheduledTaskAction -Execute {powershell_literal(background_python)} "
            f"-Argument {powershell_literal(str(watcher))} -WorkingDirectory {powershell_literal(str(HOME))}",
            "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
            "$settings = New-ScheduledTaskSettingsSet "
            "-AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries "
            "-StartWhenAvailable "
            "-MultipleInstances IgnoreNew",
            "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
            f"Register-ScheduledTask -TaskName {powershell_literal(WINDOWS_WATCHER_TASK_NAME)} "
            "-Action $action -Trigger $trigger -Settings $settings -Principal $principal "
            "-Description 'Watch Codex sessions and enqueue task-notify events' -Force | Out-Null",
            f"Start-ScheduledTask -TaskName {powershell_literal(WINDOWS_WATCHER_TASK_NAME)}",
        ]
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("Warning: failed to register Windows scheduled task for Codex watcher; falling back to Startup folder.")
        if result.stderr.strip():
            print(result.stderr.strip())


def render_windows_startup_script(background_python: str) -> str:
    sender = INSTALL_BIN / "task_notify_sender.py"
    return "\r\n".join(
        [
            "@echo off",
            f"start \"task-notify-sender\" /min \"{background_python}\" \"{sender}\"",
            "",
        ]
    )


def render_windows_watcher_startup_script(background_python: str) -> str:
    watcher = INSTALL_BIN / "task_notify_codex_session_watcher.py"
    return "\r\n".join(
        [
            "@echo off",
            f"start \"task-notify-codex-watcher\" /min \"{background_python}\" \"{watcher}\"",
            "",
        ]
    )


def bootstrap_windows_startup(background_python: str) -> None:
    if not IS_WINDOWS:
        return

    ensure_dir(WINDOWS_STARTUP_DIR)
    WINDOWS_STARTUP_SCRIPT.write_text(render_windows_startup_script(background_python), encoding="utf-8")

    if sender_running_windows():
        return

    sender = INSTALL_BIN / "task_notify_sender.py"
    subprocess.Popen(
        [background_python, str(sender)],
        cwd=str(HOME),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def bootstrap_windows_watcher_startup(background_python: str) -> None:
    if not IS_WINDOWS:
        return

    ensure_dir(WINDOWS_STARTUP_DIR)
    WINDOWS_WATCHER_STARTUP_SCRIPT.write_text(
        render_windows_watcher_startup_script(background_python),
        encoding="utf-8",
    )

    if watcher_running_windows():
        return

    watcher = INSTALL_BIN / "task_notify_codex_session_watcher.py"
    subprocess.Popen(
        [background_python, str(watcher)],
        cwd=str(HOME),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    python_executable = sys.executable
    background_python = select_background_python(python_executable)

    runtime_dirs = [INSTALL_BIN, INSTALL_LIB, CONFIG_DIR, LOG_DIR]
    if IS_DARWIN:
        runtime_dirs.extend([CODEX_DIR, LAUNCH_AGENTS_DIR])
    if IS_WINDOWS:
        runtime_dirs.append(WINDOWS_STARTUP_DIR)

    for path in runtime_dirs:
        ensure_dir(path)

    copy_tree_contents(REPO_ROOT / "bin", INSTALL_BIN)
    copy_tree_contents(REPO_ROOT / "lib", INSTALL_LIB)
    write_default_config()

    if IS_DARWIN:
        stop_command = f"{python_executable} {INSTALL_BIN / 'task_notify_codex_stop.py'}"
        merge_codex_hooks(stop_command)
        ensure_codex_hooks_feature()
        ensure_zsh_source()
        PLIST_PATH.write_text(render_plist(python_executable), encoding="utf-8")
        bootstrap_launch_agent()
    elif IS_WINDOWS:
        bootstrap_windows_task(background_python)
        bootstrap_windows_watcher_task(background_python)
        bootstrap_windows_startup(background_python)
        bootstrap_windows_watcher_startup(background_python)

    print("Installed task-notify runtime.")
    print(f"Platform: {SYSTEM}")
    print(f"Python: {python_executable}")
    print(f"Background Python: {background_python}")
    print(f"Runtime: {INSTALL_BASE}")
    print(f"Config: {CONFIG_DIR}")
    if IS_DARWIN:
        print(f"LaunchAgent: {PLIST_PATH}")
        print("Codex integration: native Stop hook")
        print("Next steps:")
        print("1. Edit ~/.config/task-notify/config.json")
        print("2. Store your SMTP auth code with ~/.local/share/task-notify/bin/task_notify_store_qq_smtp_password.sh")
        print("3. Open a new Terminal or run: source ~/.zshrc")
        print("4. Restart Codex so the Stop hook is reloaded")
    elif IS_WINDOWS:
        print("Windows background services:")
        print(f"- Sender task: {WINDOWS_TASK_NAME}")
        print(f"- Codex watcher task: {WINDOWS_WATCHER_TASK_NAME}")
        print(f"- Startup sender: {WINDOWS_STARTUP_SCRIPT}")
        print(f"- Startup watcher: {WINDOWS_WATCHER_STARTUP_SCRIPT}")
        print("Codex integration: session watcher fallback (Windows native lifecycle hooks are unavailable)")
        print("Next steps:")
        print("1. Edit ~/.config/task-notify/config.json")
        print("2. Put your SMTP auth code into ~/.config/task-notify/credentials.env")
        print("3. Log in to Windows again or run the Startup scripts once")
    else:
        print("Platform note: automatic background wiring is implemented for macOS and Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
