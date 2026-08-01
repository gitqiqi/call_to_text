#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

HISTORY_LOG=${HISTORY_LOG:-logs/history_daemon.log}
DAEMON_LOCK=${HISTORY_DAEMON_LOCK:-/tmp/call_to_text_history_daemon.lock}
HISTORY_LOCK=${HISTORY_LOCK:-/tmp/call_to_text_history.lock}
MAIN_LOCK=${MAIN_LOCK:-/tmp/call_to_text.lock}
DONE_FILE=${HISTORY_DONE_FILE:-/tmp/call_to_text_history.done}
CHECK_INTERVAL=${HISTORY_DAEMON_INTERVAL:-300}

log() {
    echo "$(date '+%F %T') $*" >> "$HISTORY_LOG"
}

history_is_running() {
    ! /usr/bin/flock -n "$HISTORY_LOCK" -c true 2>/dev/null
}

main_is_idle() {
    /usr/bin/flock -n "$MAIN_LOCK" -c true 2>/dev/null
}

exec 8>"$DAEMON_LOCK"
if ! /usr/bin/flock -n 8; then
    log "已有守护进程在运行，退出"
    exit 0
fi

log "守护进程启动"

while true; do
    if [ -f "$DONE_FILE" ]; then
        log "历史任务已完成，守护进程退出"
        exit 0
    fi

    if history_is_running; then
        sleep "$CHECK_INTERVAL"
        continue
    fi

    if main_is_idle; then
        log "主任务空闲，启动/恢复历史任务"
        nohup "$SCRIPT_DIR/run_history_smart.sh" >> "$HISTORY_LOG" 2>&1 &
    else
        log "主任务正在运行，等待..."
    fi

    sleep "$CHECK_INTERVAL"
done
