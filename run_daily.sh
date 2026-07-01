#!/usr/bin/env bash
set -e
cd /Users/cherry/Project/call_to_text
export ASR_MODEL_DIR=/Users/cherry/Project/model
.venv/bin/python call_to_text.py >> cron.log 2>&1
