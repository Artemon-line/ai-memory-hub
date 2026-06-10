# Memory Quality And Fact Layer Plan

## Goal

Make ai-memory-hub better at answering practical memory questions by moving beyond raw chunk retrieval into confidence-aware answers, compact provenance, deduplicated results, extracted facts, and explicit correction workflows.

This plan comes from real Codex to opencode MCP testing where conversation handoff worked, but the returned evidence shape showed the next set of product gaps.

## Current Status

Implemented:

- `memory_ask` returns an answer with matching results and citations.
- Search results include chunk-level provenance and source conversation payloads.
- Conversation-aware result metadata exists, including conversation score and match count.
- Ask responses can track selected and dropped chunks for token-budgeted context building.

Not implemented yet:

- A clear `confidence` field for generated answers.
- A compact provenance summary intended for agent display.
- A result mode that deduplicates repeated chunks from the same conversation.
- A normalized fact layer for user profile facts, project facts, preferences, owned items, and recurring topics.
- A correction or supersession workflow for outdated facts.
- Conflict-aware answers when multiple stored memories disagree.

## Problems Observed

1. Raw result noise

   Multiple chunks from the same conversation can dominate the result list. This is useful for debugging but noisy for an agent trying to answer a direct user question.

2. No confidence signal

   The caller cannot easily tell whether the answer is strongly supported by memory, weakly inferred, ambiguous, or unsupported.

3. Weak direct-fact recall shape

   Questions like "What guitar do I own?" or "What was the command name?" should behave like profile or project fact lookup, not only semantic chunk search.

4. No correction model

   If the user corrects a fact later, the system can insert another memory, but it has no first-class way to supersede the older fact while preserving provenance.

## Phase 1: Improve `memory_ask` Answer Shape

Add optional fields to the `memory_ask` response while preserving the existing response contract:

```json
{
  "answer": "...",
  "confidence": "high",
  "answer_basis": "direct_memory",
  "provenance": [
    {
      "conversation_id": "...",
      "source": "codex",
      "title": "Codex Velvet Lantern test",
      "stored_at": "2026-06-10T...",
      "matching_chunks": 3,
      "used_in_answer": true
    }
  ]
}
```

Proposed `confidence` values:

- `high`: answer is directly supported by one or more strong matching memories.
- `medium`: answer is supported but sparse, partial, or slightly indirect.
- `low`: memory contains possible evidence but not enough for a firm answer.
- `none`: no relevant memory was found.

Proposed `answer_basis` values:

- `direct_memory`: answer comes directly from retrieved content.
- `fact_layer`: answer comes from normalized facts.
- `mixed`: answer uses facts plus conversation retrieval.
- `not_found`: no answerable memory was found.
- `conflict`: relevant memories disagree.

## Phase 2: Add Result Compaction

Add a request option for search and ask tools:

```json
{
  "result_mode": "compact"
}
```

Supported modes:

- `chunks`: current behavior; return individual matching chunks.
- `compact`: return the best chunks but group repeated evidence by conversation.
- `conversations`: return one summarized result per conversation.

Acceptance behavior:

- Repeated chunks from one conversation should not crowd out other relevant conversations.
- Compact mode should still expose enough evidence for the agent to cite memory responsibly.
- Existing callers should continue to get `chunks` by default.

## Phase 3: Add A Fact Extraction Store

Create a normalized fact model beside conversation storage:

```json
{
  "id": "...",
  "subject": "user",
  "predicate": "owns_guitar",
  "object": "Gibson Special with P90 pickups, cherry",
  "qualifiers": {
    "instrument": "guitar",
    "color": "cherry",
    "pickup": "P90"
  },
  "confidence": "high",
  "source_conversation_id": "...",
  "source_message_indexes": [2],
  "created_at": "...",
  "updated_at": "...",
  "superseded_by": null,
  "deleted_at": null
}
```

Start with deterministic extraction rules before adding LLM extraction:

- `I have ...`
- `I own ...`
- `My favorite ... is ...`
- `The creator is ...`
- `<project> is ...`
- `The command name is ...`
- `The indexing strategy is ...`

Initial fact categories:

- User owned items.
- User preferences.
- User identity/profile notes.
- Project names and project attributes.
- People associated with projects.
- Recurring topics.

## Phase 4: Add Profile And Fact Tools

Add MCP tools after the fact model is stable:

- `memory_fact_search`
- `memory_profile_get`
- `memory_fact_supersede`

Example profile response:

```json
{
  "subject": "user",
  "facts": [
    {
      "predicate": "owns_guitar",
      "object": "Gibson Special with P90 pickups, cherry",
      "confidence": "high",
      "source_conversation_id": "..."
    }
  ]
}
```

## Phase 5: Correction And Supersession

Corrections should preserve history instead of deleting old memories by default.

Example flow:

1. User says: "Actually, my Gibson is TV yellow, not cherry."
2. The server creates a new fact.
3. The old fact is marked with `superseded_by`.
4. Future answers prefer the newer fact.
5. If old and new facts both appear relevant, the answer mentions the correction.

Required behavior:

- Superseded facts are excluded from normal profile answers.
- Superseded facts remain available for audit and provenance.
- Conflicting active facts produce a `conflict` answer basis.

## Phase 6: Answer Policy

Use facts first for direct profile questions:

- "What guitar do I own?"
- "What is my favorite command name?"
- "Who created Velvet Lantern?"

Use conversation retrieval first for broader recall questions:

- "What was my recent Codex conversation about?"
- "What did we discuss about Velvet Lantern?"
- "Summarize recent project ideas."

Use mixed mode when a direct fact answer needs conversation context.

## Acceptance Criteria

- `memory_ask` can return `confidence`, `answer_basis`, and compact provenance.
- Compact result mode prevents repeated chunks from one conversation dominating the response.
- A direct fact question can be answered from the fact layer with source provenance.
- User corrections can supersede older facts without deleting their source memories.
- Conflicting facts produce a cautious answer with citations instead of a silent guess.
- Existing MCP clients remain compatible with the current response fields.
