if [[ -n "${TASK_NOTIFY_ZSH_LOADED:-}" ]]; then
  return
fi
export TASK_NOTIFY_ZSH_LOADED=1

if [[ $- != *i* ]]; then
  return
fi

if ! command -v python3 >/dev/null 2>&1; then
  return
fi

zmodload zsh/datetime 2>/dev/null || true
autoload -Uz add-zsh-hook

typeset -g TASK_NOTIFY_ENQUEUE_SCRIPT="$HOME/.local/share/task-notify/bin/task_notify_enqueue.py"
typeset -g TASK_NOTIFY_SHELL_SESSION_ID="${TASK_NOTIFY_SHELL_SESSION_ID:-${HOST:-$(hostname)}-$$-$(date +%s)}"
typeset -g TASK_NOTIFY_ACTIVE_COMMAND=""
typeset -g TASK_NOTIFY_ACTIVE_START="0"
typeset -g TASK_NOTIFY_ACTIVE_CWD=""

task_notify_preexec() {
  TASK_NOTIFY_ACTIVE_COMMAND="$1"
  TASK_NOTIFY_ACTIVE_START="${EPOCHREALTIME:-0}"
  TASK_NOTIFY_ACTIVE_CWD="$PWD"
}

task_notify_precmd() {
  local exit_code=$?
  local command="$TASK_NOTIFY_ACTIVE_COMMAND"
  local start_epoch="$TASK_NOTIFY_ACTIVE_START"
  local command_cwd="$TASK_NOTIFY_ACTIVE_CWD"

  TASK_NOTIFY_ACTIVE_COMMAND=""
  TASK_NOTIFY_ACTIVE_START="0"
  TASK_NOTIFY_ACTIVE_CWD=""

  if [[ -z "$command" || "$start_epoch" == "0" ]]; then
    return
  fi

  command python3 "$TASK_NOTIFY_ENQUEUE_SCRIPT" shell-finish \
    --command "$command" \
    --cwd "$command_cwd" \
    --exit-code "$exit_code" \
    --start-epoch "$start_epoch" \
    --shell-pid "$$" \
    --shell-session-id "$TASK_NOTIFY_SHELL_SESSION_ID" \
    >/dev/null 2>&1
}

add-zsh-hook preexec task_notify_preexec
add-zsh-hook precmd task_notify_precmd
