# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local-first memory for AI agents.

`ai-memory-hub` gives Codex, opencode, Claude, Copilot, and other agent clients a
shared memory backend through MCP and HTTP. Store conversations once, then search,
retrieve, and ask over them from any compatible client.

## Why It Exists

Agent conversations contain useful project context, preferences, decisions, and
facts, but that memory is usually trapped inside one tool. ai-memory-hub keeps it
in your own local storage and exposes it through stable interfaces.

It is built for:

- Cross-client handoff between agent tools
- Local-first project and profile memory
- MCP-native workflows
- Deterministic ingestion and retrieval
- SQLite/LanceDB by default, Postgres/PGVector when you want it

## What It Does

- Stores structured conversations with validation and deduplication
- Searches semantic memory with conversation-aware grouping
- Answers questions with citations, confidence, and provenance
- Extracts useful profile/project facts for direct answers
- Serves both FastAPI endpoints and an MCP server
- Runs locally, in containers, or against Postgres/PGVector

## Quick Start

Install:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
```

Start the API and MCP server:

```bash
uv run aim serve --host 127.0.0.1 --port 8000
```

Insert a memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "source": "codex",
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Stored."}
    ],
    "metadata": {"tags": ["preferences"]}
  }'
```

Ask over memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What tool preference did the user mention?", "top_k": 5}'
```

MCP endpoint:

```text
http://127.0.0.1:8000/mcp/
```

## Common Workflows

Use the CLI during development:

```bash
uv run aim ingest conversation.json --json
uv run aim search "local-first tools" --top-k 5 --json
uv run aim ask "What did I store about local-first tools?" --top-k 5 --json
```

Run with Docker:

```bash
docker build -t ai-memory-hub:local -f Containerfile .
docker run --rm -p 8000:8000 ai-memory-hub:local
```

Run with Postgres and PGVector:

```bash
cd examples/postgres/pgvector
docker compose up --build
```

The Compose example is unauthenticated for local smoke testing. Before exposing
it on a LAN, create a bearer token in the metadata database and change
`api.auth` in the mounted config to `bearer_token`; clients then send
`Authorization: Bearer <token>` to both HTTP and MCP endpoints. See the PGVector
runbook in the checked-in Compose example directory for the exact commands.

## Documentation

- [Technical overview](docs/overview.md)
- [Architecture](docs/architecture.md)
- [Agent integration](docs/agents.md)
- [MCP plan](docs/mcp_plan.md)
- [Real-client MCP smoke plan](docs/real_client_mcp_smoke_plan.md)
- [Roadmap](docs/roadmap.md)
- [Improvements](docs/improvements.md)

Build the docs locally:

```bash
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

## Status

The project currently includes deterministic ingestion, MCP tools/resources/prompts,
HTTP endpoints, CLI commands, fact-backed answers, SQLite/Postgres metadata,
LanceDB/PGVector/in-memory vectors, token-budgeted ask, container CI, and GitHub
Pages docs publishing.

Planned work includes broader importers, richer summaries, deletion/update
workflows, more vector providers, and release publishing.

## Contributing

Contributions are welcome. Keep changes focused, add tests for behavior changes,
and run the relevant checks before opening a pull request.

```bash
uv run python -m ruff check memory tests tools
uv run pytest
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.
