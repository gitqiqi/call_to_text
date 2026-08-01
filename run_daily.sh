#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

mkdir -p logs

if [ -x venv/bin/python ]; then
  PYTHON_BIN=venv/bin/python
elif [ -x .venv/bin/python ]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN=python
fi

export ASR_MODEL_DIR=${ASR_MODEL_DIR:-$SCRIPT_DIR/models}
export ASR_MODEL=${ASR_MODEL:-sensevoice}
export ASR_SEGMENT_MODE=${ASR_SEGMENT_MODE:-fast}
export SENSEVOICE_LANGUAGE=${SENSEVOICE_LANGUAGE:-zh}
export LOOKBACK_DAYS=${LOOKBACK_DAYS:-3}
export INSERT_BATCH_SIZE=${INSERT_BATCH_SIZE:-100}
export KEEP_AUDIO=${KEEP_AUDIO:-0}
export CLEANUP_AUDIO_HOURS=${CLEANUP_AUDIO_HOURS:-24}
export PROGRESS_EVERY=${PROGRESS_EVERY:-100}
export LOG_RECORDS=${LOG_RECORDS:-0}
export LOG_STAGES=${LOG_STAGES:-1}
export ASR_OUTPUT_TIMESTAMP=${ASR_OUTPUT_TIMESTAMP:-1}
export SHARD_COUNT=${SHARD_COUNT:-1}
export SHARD_INDEX=${SHARD_INDEX:-0}

LOG_FILE="logs/call_to_text_$(date +%F).log"
DATE_WINDOW="LOOKBACK_DAYS=$LOOKBACK_DAYS"
if [ -n "${START_DATE:-}" ] || [ -n "${END_DATE:-}" ]; then
  DATE_WINDOW="START_DATE=${START_DATE:-} END_DATE=${END_DATE:-}"
fi

echo "$(date '+%F %T') START $DATE_WINDOW ASR_MODEL=$ASR_MODEL ASR_DEVICE=${ASR_DEVICE:-} SHARD_COUNT=$SHARD_COUNT SHARD_INDEX=$SHARD_INDEX" >> "$LOG_FILE"
if [ "${COUNT_PENDING_ONLY:-0}" = "1" ]; then
  "$PYTHON_BIN" call_to_text.py 2>&1 | tee -a "$LOG_FILE"
else
  "$PYTHON_BIN" call_to_text.py >> "$LOG_FILE" 2>&1
fi
