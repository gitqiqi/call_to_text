#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

BATCH_DAYS=${1:-${HISTORY_BATCH_DAYS:-10}}
TOTAL_DAYS=${2:-${HISTORY_TOTAL_DAYS:-1000}}
MAIN_LOOKBACK_DAYS=${HISTORY_SKIP_RECENT_DAYS:-3}
STATE_FILE=${HISTORY_STATE_FILE:-/tmp/history_progress.txt}
DONE_FILE=${HISTORY_DONE_FILE:-/tmp/call_to_text_history.done}
HISTORY_LOCK=${HISTORY_LOCK:-/tmp/call_to_text_history.lock}
MAIN_LOCK=${MAIN_LOCK:-/tmp/call_to_text.lock}
HISTORY_LOG=${HISTORY_LOG:-logs/history_smart.log}
ASR_MODEL=${ASR_MODEL:-sensevoice}
ASR_DEVICE=${ASR_DEVICE:-cuda:0}
HISTORY_RECORD_TIMEOUT_SECONDS=${HISTORY_RECORD_TIMEOUT_SECONDS:-600}
HISTORY_STOP_TIME=${HISTORY_STOP_TIME:-23:30}
HISTORY_RESUME_TIME=${HISTORY_RESUME_TIME:-00:40}
MIN_BATCH_SECONDS=${HISTORY_MIN_BATCH_SECONDS:-600}
RETRY_SLEEP_SECONDS=${HISTORY_RETRY_SLEEP_SECONDS:-1800}

log() {
    echo "$(date '+%F %T') $*" >> "$HISTORY_LOG"
}

date_add_days() {
    date -d "$1 + $2 days" +%F
}

date_to_epoch() {
    date -d "$1" +%s
}

min_date() {
    if [ "$(date_to_epoch "$1")" -le "$(date_to_epoch "$2")" ]; then
        echo "$1"
    else
        echo "$2"
    fi
}

valid_date() {
    date -d "$1" +%F >/dev/null 2>&1
}

is_positive_int() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]]
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

seconds_until_stop() {
    local now stop
    now=$(current_minutes)
    stop=$(time_to_minutes "$HISTORY_STOP_TIME")
    if ((stop <= now)); then
        echo 0
    else
        echo $(((stop - now) * 60))
    fi
}

pending_count_for_window() {
    local window_start=$1
    local window_end=$2
    local output status count

    output=$(
        /usr/bin/env \
            START_DATE="$window_start" \
            END_DATE="$window_end" \
            COUNT_PENDING_ONLY=1 \
            ASR_MODEL="$ASR_MODEL" \
            ASR_DEVICE="$ASR_DEVICE" \
            "$SCRIPT_DIR/run_daily.sh" 2>&1
    )
    status=$?
    printf '%s\n' "$output" >> "$HISTORY_LOG"
    if [ "$status" -ne 0 ]; then
        return "$status"
    fi

    count=$(printf '%s\n' "$output" | awk -F= '/^PENDING_COUNT=/{value=$2} END{print value}')
    if [ -z "$count" ]; then
        log "无法解析待处理数量: $window_start <= msg_time < $window_end"
        return 1
    fi
    echo "$count"
}

run_window() {
    local window_start=$1
    local window_end=$2
    local seconds_left=$3
    local timeout_cmd=()

    if command -v timeout >/dev/null 2>&1; then
        timeout_cmd=(timeout "$seconds_left")
    fi

    "${timeout_cmd[@]}" /usr/bin/flock -w 60 "$MAIN_LOCK" \
        /usr/bin/env \
            START_DATE="$window_start" \
            END_DATE="$window_end" \
            ASR_MODEL="$ASR_MODEL" \
            ASR_DEVICE="$ASR_DEVICE" \
            ASR_CHUNK_SECONDS=0 \
            RECORD_TIMEOUT_SECONDS="$HISTORY_RECORD_TIMEOUT_SECONDS" \
            "$SCRIPT_DIR/run_daily.sh"
}

exec 9>"$HISTORY_LOCK"
if ! /usr/bin/flock -n 9; then
    log "已有历史任务在运行，退出"
    exit 0
fi

if [ -f "$DONE_FILE" ]; then
    log "历史任务已标记完成，如需重跑请删除 $DONE_FILE"
    exit 0
fi

if ! is_positive_int "$BATCH_DAYS"; then
    log "HISTORY_BATCH_DAYS 必须大于 0"
    exit 1
fi

if ! is_positive_int "$TOTAL_DAYS"; then
    log "HISTORY_TOTAL_DAYS 必须大于 0"
    exit 1
fi

if ! is_positive_int "$MAIN_LOOKBACK_DAYS"; then
    log "HISTORY_SKIP_RECENT_DAYS 必须大于 0"
    exit 1
fi

if ! is_positive_int "$MIN_BATCH_SECONDS"; then
    log "HISTORY_MIN_BATCH_SECONDS 必须大于 0"
    exit 1
fi

if ! is_positive_int "$RETRY_SLEEP_SECONDS"; then
    log "HISTORY_RETRY_SLEEP_SECONDS 必须大于 0"
    exit 1
fi

if ! time_to_minutes "$HISTORY_STOP_TIME" >/dev/null; then
    log "HISTORY_STOP_TIME 必须使用 HH:MM 格式"
    exit 1
fi

if ! time_to_minutes "$HISTORY_RESUME_TIME" >/dev/null; then
    log "HISTORY_RESUME_TIME 必须使用 HH:MM 格式"
    exit 1
fi

HISTORY_END_DATE=${HISTORY_END_DATE:-$(date -d "$(date +%F) - $MAIN_LOOKBACK_DAYS days" +%F)}
HISTORY_START_DATE=${HISTORY_START_DATE:-$(date -d "$HISTORY_END_DATE - $TOTAL_DAYS days" +%F)}

if ! valid_date "$HISTORY_START_DATE" || ! valid_date "$HISTORY_END_DATE"; then
    log "HISTORY_START_DATE/HISTORY_END_DATE 必须使用 YYYY-MM-DD 格式"
    exit 1
fi

if [ "$(date_to_epoch "$HISTORY_START_DATE")" -ge "$(date_to_epoch "$HISTORY_END_DATE")" ]; then
    log "HISTORY_START_DATE 必须早于 HISTORY_END_DATE"
    exit 1
fi

if [ -f "$STATE_FILE" ]; then
    CURRENT_START=$(cat "$STATE_FILE" 2>/dev/null || true)
    if ! valid_date "$CURRENT_START"; then
        log "进度文件内容无效，重新从 $HISTORY_START_DATE 开始: $STATE_FILE"
        CURRENT_START=$HISTORY_START_DATE
        echo "$CURRENT_START" > "$STATE_FILE"
    fi
else
    CURRENT_START=$HISTORY_START_DATE
    echo "$CURRENT_START" > "$STATE_FILE"
fi

log "========== 历史任务启动: $CURRENT_START -> $HISTORY_END_DATE, batch=${BATCH_DAYS}d =========="

while true; do
    if in_history_pause_window; then
        log "处于主任务保护窗口 $HISTORY_STOP_TIME-$HISTORY_RESUME_TIME，退出等待下次恢复"
        exit 0
    fi

    CURRENT_START=$(cat "$STATE_FILE" 2>/dev/null || echo "$HISTORY_START_DATE")

    if [ "$(date_to_epoch "$CURRENT_START")" -ge "$(date_to_epoch "$HISTORY_END_DATE")" ]; then
        log "所有历史数据处理完成: $HISTORY_START_DATE <= msg_time < $HISTORY_END_DATE"
        rm -f "$STATE_FILE"
        echo "$(date '+%F %T')" > "$DONE_FILE"
        break
    fi

    SECONDS_LEFT=$(seconds_until_stop)
    if [ "$SECONDS_LEFT" -lt "$MIN_BATCH_SECONDS" ]; then
        log "距离让路时间 $HISTORY_STOP_TIME 不足 $((MIN_BATCH_SECONDS / 60)) 分钟，退出等待下次恢复"
        exit 0
    fi

    BATCH_END=$(min_date "$(date_add_days "$CURRENT_START" "$BATCH_DAYS")" "$HISTORY_END_DATE")
    log "开始处理窗口: $CURRENT_START <= msg_time < $BATCH_END, 最多运行 $((SECONDS_LEFT / 60)) 分钟"

    run_window "$CURRENT_START" "$BATCH_END" "$SECONDS_LEFT"
    RUN_STATUS=$?

    if [ "$RUN_STATUS" -eq 124 ]; then
        log "窗口处理达到时间上限，保留进度 $CURRENT_START，等待下次继续"
        exit 0
    fi

    if [ "$RUN_STATUS" -ne 0 ]; then
        log "窗口处理失败 status=$RUN_STATUS，保留进度 $CURRENT_START，$((RETRY_SLEEP_SECONDS / 60)) 分钟后重试"
        sleep "$RETRY_SLEEP_SECONDS"
        continue
    fi

    PENDING_COUNT=$(pending_count_for_window "$CURRENT_START" "$BATCH_END")
    COUNT_STATUS=$?
    if [ "$COUNT_STATUS" -ne 0 ]; then
        log "待处理数量检查失败，保留进度 $CURRENT_START，$((RETRY_SLEEP_SECONDS / 60)) 分钟后重试"
        sleep "$RETRY_SLEEP_SECONDS"
        continue
    fi

    if [ "$PENDING_COUNT" -eq 0 ]; then
        echo "$BATCH_END" > "$STATE_FILE"
        log "窗口完成，进度推进到 $BATCH_END"
    else
        log "窗口仍有 $PENDING_COUNT 条待处理，继续重跑当前窗口"
    fi

    sleep 10
done

log "========== 历史任务结束 =========="
