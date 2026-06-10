# Compatible with Podman and Docker.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev --extra postgres --extra tokenizer && \
    mkdir -p "$TIKTOKEN_CACHE_DIR" && \
    uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY memory ./memory
COPY example.config.yaml /app/config.yaml

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "memory.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
