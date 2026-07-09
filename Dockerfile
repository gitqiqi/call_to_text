FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libsndfile1 \
    ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project && uv cache clean

COPY call_to_text.py .

ENV ASR_MODEL=sensevoice
ENV WHISPER_MODEL=base
ENV ASR_SEGMENT_MODE=fast
ENV ASR_OUTPUT_TIMESTAMP=1
ENV SENSEVOICE_LANGUAGE=zh
ENV ASR_HOTWORDS=
ENV INSERT_BATCH_SIZE=100
ENV KEEP_AUDIO=0
ENV PROGRESS_EVERY=0
ENV LOG_RECORDS=0
ENV ASR_MODEL_DIR=/app/models

VOLUME ["/app/models", "/app/MP3"]

CMD ["python", "call_to_text.py"]
