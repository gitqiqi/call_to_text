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
HISTORY_STOP_TIME=${HISTORY_STOP_TIME:-23:30}
HISTORY_RESUME_TIME=${HISTORY_RESUME_TIME:-00:40}

log() {
    echo "$(date '+%F %T') $*" >> "$HISTORY_LOG"
}

history_is_running() {
    ! /usr/bin/flock -n "$HISTORY_LOCK" -c true 2>/dev/null
}

main_is_idle() {
    /usr/bin/flock -n "$MAIN_LOCK" -c true 2>/dev/null
}

time_to_minutes() {
    local value=$1
    if [[ ! "$value" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
        return 1
    fi
    local hour=${BASH_REMATCH[1]}
    local minute=${BASH_REMATCH[2]}
    if ((10#$hour > 23 || 10#$minute > 59)); then
        return 1
    fi
    echo $((10#$hour * 60 + 10#$minute))
}

current_minutes() {
    local hour minute
    hour=$(date +%H)
    minute=$(date +%M)
    echo $((10#$hour * 60 + 10#$minute))
}

in_history_pause_window() {
    local now stop resume
    now=$(current_minutes)
    stop=$(time_to_minutes "$HISTORY_STOP_TIME") || return 1
    resume=$(time_to_minutes "$HISTORY_RESUME_TIME") || return 1

    if ((stop < resume)); then
        ((now >= stop || now < resume))
    else
        ((now >= stop && now < resume))
    fi
}

exec 8>"$DAEMON_LOCK"
if ! /usr/bin/flock -n 8; then
    log "已有守护进程在运行，退出"
    exit 0
fi

log "守护进程启动"

if ! time_to_minutes "$HISTORY_STOP_TIME" >/dev/null; then
    log "HISTORY_STOP_TIME 必须使用 HH:MM 格式"
    exit 1
fi

if ! time_to_minutes "$HISTORY_RESUME_TIME" >/dev/null; then
    log "HISTORY_RESUME_TIME 必须使用 HH:MM 格式"
    exit 1
fi

while true; do
    if [ -f "$DONE_FILE" ]; then
        log "历史任务已完成，守护进程退出"
        exit 0
    fi

    if in_history_pause_window; then
        log "处于主任务保护窗口 $HISTORY_STOP_TIME-$HISTORY_RESUME_TIME，暂停启动历史任务"
        sleep "$CHECK_INTERVAL"
        continue
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
