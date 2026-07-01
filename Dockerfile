FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project && uv cache clean

COPY call_to_text.py .

ENV ASR_MODEL=whisper
ENV WHISPER_MODEL=base
ENV ASR_MODEL_DIR=/app/models

VOLUME ["/app/models", "/app/MP3"]

CMD ["python", "call_to_text.py"]
