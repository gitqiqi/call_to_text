#!/usr/bin/env bash
set -e
cd /Users/cherry/Project/call_to_text
export ASR_MODEL_DIR=/Users/cherry/Project/model
export ASR_MODEL=${ASR_MODEL:-sensevoice}
export ASR_SEGMENT_MODE=${ASR_SEGMENT_MODE:-fast}
export SENSEVOICE_LANGUAGE=${SENSEVOICE_LANGUAGE:-zh}
export INSERT_BATCH_SIZE=${INSERT_BATCH_SIZE:-100}
export KEEP_AUDIO=${KEEP_AUDIO:-0}
export PROGRESS_EVERY=${PROGRESS_EVERY:-0}
export LOG_RECORDS=${LOG_RECORDS:-0}
export LOG_STAGES=${LOG_STAGES:-1}
export ASR_OUTPUT_TIMESTAMP=${ASR_OUTPUT_TIMESTAMP:-1}
.venv/bin/python call_to_text.py >> cron.log 2>&1
