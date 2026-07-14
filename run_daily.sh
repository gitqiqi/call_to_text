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
"$PYTHON_BIN" call_to_text.py >> "logs/call_to_text_$(date +%F).log" 2>&1
