FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEMORY_CONFIG=/app/config.yaml

WORKDIR /app

RUN python -m pip install --upgrade pip && \
    pip install \
      fastapi>=0.135.2 \
      jsonschema>=4.25.1 \
      lancedb>=0.30.1 \
      openai>=2.30.0 \
      pydantic>=2.12.5 \
      python-dotenv>=1.2.2 \
      uvicorn>=0.42.0

COPY memory ./memory
COPY example.config.yaml /app/config.yaml
COPY README.md ./README.md

EXPOSE 8000 8765

CMD ["python", "-m", "memory.cli", "--config", "/app/config.yaml"]
