# Multi-Modal Ingestion Plan: From Raw Text to Structured Memory

## 1. Overview
The goal is to transform `ai-memory-hub` from a structured MCP server into a flexible ingestion engine that can handle memory from any source, regardless of its native technical capabilities.

## 2. Ingestion Personas & Paths

| Path | Source | Type | Processing |
| :--- | :--- | :--- | :--- |
| **Direct MCP** | Codex, Claude, Custom MCP clients | Structured JSON | Validation + Normalization |
| **JSON Paste** | LLM Web UI (Manual copy-paste) | Semi-Structured JSON | Validation Only |
| **Raw Text Paste** | Browser, PDF, Slack, Old Logs | Unstructured Text | LLM-based Parsing -> JSON |
| **Agentic Capture** | "Ask Codex to save this raw text" | Unstructured Text | In-tool LLM Parsing -> MCP call |

---

## 3. Detailed Workflow Designs

### 3.1 Path A: The "Validator" (JSON Paste)
*   **User Flow**: User copies a formatted JSON block from a "Memory Formatter" agent and pastes it into a Hub UI or `curl` command.
*   **Hub Action**: Skips parsing. Runs strictly against the `conversation.schema.json`.
*   **Benefit**: Zero-cost ingestion for power users.

### 3.2 Path B: The "Interpreter" (Raw Text Paste)
*   **User Flow**: User copies a messy conversation from a browser and hits an endpoint `/ingest/raw`.
*   **Hub Action**: 
    1.  Hub calls an internal "Parsing Agent" (using the configured LLM, e.g., Ollama or OpenAI).
    2.  LLM extracts roles (user/assistant) and timestamps.
    3.  Output is fed into the standard `memory_insert` pipeline.
*   **Prompt Strategy**: A deterministic system prompt that enforces the Hub's schema.

### 3.3 Path C: The "Proxy" (Ask Codex / Agentic Raw)
*   **Use Case**: User says to Codex: *"Hey, here is a raw log of a meeting I had elsewhere. Store this as memory."*
*   **Workflow**:
    1.  Codex receives raw text.
    2.  Codex uses its own internal LLM to structure the data (or calls a specific Hub tool `memory_parse_raw`).
    3.  Codex calls `memory_insert` via the MCP connection.
*   **Staff Recommendation**: Expose a `memory_parse_raw(text: str)` tool via MCP. This allows the Agent to use the Hub's preferred parsing logic instead of making it up.

---

## 4. Architectural Enhancements

### 4.1 New Hub Components
1.  **Ingestion Bridge**: A new module `memory/ingestion/bridge.py` to route different input types.
2.  **Parsing LLM Wrapper**: A lightweight utility to use the configured embedding provider's "Chat" sibling (e.g., `nomic-embed` + `llama3`) for text structuring.

### 4.2 API Extensions
*   `POST /v1/ingest/raw`: Accepts `{"text": "...", "metadata_hint": {}}`.
*   `POST /v1/ingest/json`: Accepts the raw schema.

---

## 5. Implementation Roadmap

### Phase 1: The Bridge (Short Term)
*   Refactor `MVPIngestionAgent` to support `ingest_raw(text)`.
*   Implement `memory_parse_raw` MCP tool.

### Phase 2: Agentic Intelligence (Mid Term)
*   Integrate a local LLM (Ollama) as the default "Parser" for raw text.
*   Add "Confidence Scores" to ingested memories (Manual vs. AI-Parsed).

### Phase 3: Browser Extension / UI (Long Term)
*   A simple UI overlay to "Clip to Memory Hub" from any webpage.

---

## 6. Staff Engineer Verdict
Supporting raw text is the "killer feature" for adoption. Users will not manually format JSON. By moving the **structuring logic** into the Hub (or exposing it as a tool), we ensure that memory remains high-quality and searchable, regardless of where it started.
