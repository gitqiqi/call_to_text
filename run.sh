#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

export $(grep -v '^\s*#' .env | grep -v '^\s*$' | xargs)

docker run --rm \
  -e DB_HOLOGRES_HOST="$DB_HOLOGRES_HOST" \
  -e DB_HOLOGRES_PORT="$DB_HOLOGRES_PORT" \
  -e DB_HOLOGRES_USER="$DB_HOLOGRES_USER" \
  -e DB_HOLOGRES_PASSWORD="$DB_HOLOGRES_PASSWORD" \
  -e DB_HOLOGRES_DATABASE="$DB_HOLOGRES_DATABASE" \
  -e ASR_MODEL="$ASR_MODEL" \
  -e WHISPER_MODEL="$WHISPER_MODEL" \
  -e ASR_MODEL_DIR=/app/models \
  -v "$(pwd)/models:/app/models" \
  -v "$(pwd)/MP3:/app/MP3" \
  call-to-text
