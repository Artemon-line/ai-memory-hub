# Release Promotion Assets

This file holds the copy-and-paste assets for the first public release. Keep it
aligned with shipped behavior only; do not advertise unreleased browser
extensions, hosted sync, UI dashboards, or SDKs.

## GitHub Repository Settings

Repository description:

```text
Local-first memory service for AI agents, with HTTP and MCP APIs.
```

Recommended topics:

```text
mcp
ai-agents
memory
rag
fastapi
pgvector
local-first
openai-compatible
```

Pin the first release announcement issue or discussion after `v0.1.0` is
published.

## Pinned Announcement Draft

Title:

```text
ai-memory-hub v0.1.0: local-first memory for AI agents
```

Body:

````text
ai-memory-hub v0.1.0 is the first public release of a local-first memory service
for AI agents.

It gives Codex, opencode, Claude, Copilot, and other MCP/HTTP-capable clients a
shared backend for storing conversations, searching memory, retrieving cited
context, and answering questions with provenance.

What ships:
- HTTP memory API and streamable HTTP MCP tools
- SQLite/LanceDB local default
- Postgres/PGVector runtime option
- deterministic ingestion, search, retrieve, ask, facts, and summaries
- CLI and container runtime
- bearer-token auth and project workspace boundaries

Try it:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
uv run aim serve --host 127.0.0.1 --port 8000
curl -fsS http://127.0.0.1:8000/ready
```

Docker:

```bash
docker run --rm -p 127.0.0.1:8000:8000 docker.io/<namespace>/ai-memory-hub:v0.1.0
curl -fsS http://127.0.0.1:8000/ready
```

Known limits:
- no hosted memory service is included
- production-quality retrieval requires your own embedding model
- browser extensions are future adapters, not part of this release
- UI dashboards and SDKs are future work

Feedback wanted:
- Which MCP clients should get first-class setup docs next?
- Which storage provider is most important for your deployment?
- What memory review/deletion workflow would make this safer for daily use?
````

## Terminal Transcript Demo

Use this as the first no-video demo asset. Update response snippets if output
shape changes before release.

```text
$ uv run aim serve --host 127.0.0.1 --port 8000
ai-memory-hub | RUNNING host=127.0.0.1 port=8000

$ curl -fsS http://127.0.0.1:8000/ready
{"status":"ok",...}

$ curl -fsS -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Got it."}
    ],
    "metadata": {
      "source": "release-demo",
      "save_intent": "explicit_user_request",
      "tags": ["demo"]
    }
  }'
{"conversation_id":"...","status":"inserted",...}

$ curl -fsS -X POST http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What tool preference did the user mention?","top_k":5}'
{
  "answer": "The user prefers local-first tools.",
  "confidence": "high",
  "answer_basis": "fact_layer",
  "citations": [...]
}
```

## Launch-Day Post Draft

```text
ai-memory-hub v0.1.0 is out.

It is a local-first memory service for AI agents: store conversations once, then
search, retrieve, and ask over them from MCP and HTTP clients.

Why it matters:
- Agent memory should not be trapped inside one client.
- Local-first storage keeps the user in control of conversation history.
- MCP makes the same memory usable from Codex, opencode, Claude, Copilot, and
  other tools.

Try it:
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
uv run aim serve --host 127.0.0.1 --port 8000

Docs:
https://artemon-line.github.io/ai-memory-hub/

Docker:
docker.io/<namespace>/ai-memory-hub:v0.1.0

Useful feedback:
- Which agent client should get the next setup guide?
- Which storage backend should be hardened next?
- What review/deletion workflow would make this safer for personal memory?
```

## Follow-Up Technical Post Draft

```text
Why ai-memory-hub separates capture, ingestion, retrieval, and context injection

The hub is the memory service. Adapters are thin edges around it.

Capture is when an adapter sends selected conversation, page, note, or code
context into the hub.

Ingestion is the internal pipeline: validate, normalize, deduplicate, enrich,
embed, and store.

Retrieval is the hub returning relevant conversations, facts, citations, or
answers.

Context injection is the adapter placing retrieved memory into the active agent
session.

That boundary is deliberate:
- clients do not own memory
- storage/auth/permissions live in one service
- memory quality fixes improve every adapter
- MCP and HTTP clients can share the same recall layer

The first release ships the service side: API, MCP tools, CLI, Docker runtime,
SQLite/LanceDB defaults, Postgres/PGVector option, bearer auth, facts, citations,
and provenance.

The next useful work is adapter polish: clearer client setup docs, safer save
intent defaults, and review/delete workflows for long-lived memory.
```
