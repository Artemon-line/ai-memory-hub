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
    UV_CACHE_DIR=/app/.uv-cache

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN python -m pip install --no-cache-dir "uv==0.10.3" && \
    uv sync --frozen --no-dev --no-install-project

COPY memory ./memory
COPY examples/container/config.yaml /app/config.yaml

RUN uv sync --frozen --no-dev && \
    uv pip install --no-deps . && \
    test -x /app/.venv/bin/aim && \
    /app/.venv/bin/python -c "import memory; from memory.cli import main; assert callable(main)" && \
    mkdir -p /app/data /app/logs /app/.uv-cache && \
    useradd --uid 1001 --gid 0 --home-dir /tmp --no-create-home \
      --shell /usr/sbin/nologin ai-memory-hub && \
    chgrp -R 0 /app && \
    chmod -R g=u /app && \
    chown -R 1001:0 /tmp/.uv-cache || true && \
    chown -R 1001:0 /app/.uv-cache && \
    chmod -R g=u /app/.uv-cache

EXPOSE 8000

USER 1001

CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]
