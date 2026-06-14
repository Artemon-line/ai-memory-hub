# Compatible with Podman and Docker.
FROM python:3.14-slim

ARG VERSION=0.1.0
ARG REVISION=unknown
ARG SOURCE=https://github.com/Artemon-line/ai-memory-hub

LABEL org.opencontainers.image.title="ai-memory-hub" \
      org.opencontainers.image.description="Local-first deterministic memory ingestion hub with FastAPI and MCP interfaces" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN python -m pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev --extra postgres --extra tokenizer && \
    mkdir -p "$TIKTOKEN_CACHE_DIR" && \
    uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY memory ./memory
COPY example.config.yaml /app/config.yaml

RUN mkdir -p /app/data /app/logs "$TIKTOKEN_CACHE_DIR" && \
    chgrp -R 0 /app "$TIKTOKEN_CACHE_DIR" && \
    chmod -R g=u /app "$TIKTOKEN_CACHE_DIR"

EXPOSE 8000

USER 1001

CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]
