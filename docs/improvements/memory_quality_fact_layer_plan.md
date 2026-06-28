# Memory Quality And Fact Layer Plan

## Goal

Make ai-memory-hub better at answering practical memory questions by moving beyond raw chunk retrieval into confidence-aware answers, compact provenance, deduplicated results, extracted facts, and explicit correction workflows.

This plan comes from real Codex to opencode MCP testing where conversation handoff worked, but the returned evidence shape showed the next set of product gaps.

## Current Status

Implemented:

- [x] `memory_ask` returns an answer with matching results and citations.
- [x] Search results include chunk-level provenance and source conversation payloads.
- [x] Conversation-aware result metadata exists, including conversation score and match count.
- [x] Ask responses can track selected and dropped chunks for token-budgeted context building.
- [x] `memory_ask` returns `confidence`, `answer_basis`, and compact `provenance`.
- [x] `memory_search` and `memory_ask` accept `result_mode` with `chunks`, `compact`, and `conversations`.
- [x] SQLite, Postgres, and MongoDB metadata stores persist deterministic extracted facts.
- [x] MCP and HTTP expose fact search, profile get, and fact supersession operations.
- [x] Direct fact questions can use the fact layer before conversation retrieval.
- [x] Superseded facts are hidden from normal profile/fact answers and retained for audit.
- [x] Conflicting active facts produce `answer_basis: "conflict"`.

Implemented since the initial fact-layer baseline:

- [x] LLM-assisted fact extraction hook beyond the initial deterministic rules.
- [x] Rich subject/entity resolution for project-specific fact questions.
- [x] Recurring-topic fact extraction.
- [x] CLI workflows for reviewing and superseding facts.

Remaining work:

- [ ] Hosted LLM extractor implementation with prompt/schema tuning.
- [ ] UI workflows for fact review, edit/supersession, and audit.

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

- [x] Add optional fields to the `memory_ask` response while preserving the existing response contract:

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

Implemented `confidence` values:

- [x] `high`: answer is directly supported by one or more strong matching memories.
- [x] `medium`: answer is supported but sparse, partial, or slightly indirect.
- [x] `low`: memory contains possible evidence but not enough for a firm answer.
- [x] `none`: no relevant memory was found.

Implemented `answer_basis` values:

- [x] `direct_memory`: answer comes directly from retrieved content.
- [x] `fact_layer`: answer comes from normalized facts.
- [x] `mixed`: answer uses facts plus conversation retrieval.
- [x] `not_found`: no answerable memory was found.
- [x] `conflict`: relevant memories disagree.

## Phase 2: Add Result Compaction

- [x] Add a request option for search and ask tools:

```json
{
  "result_mode": "compact"
}
```

Supported modes:

- [x] `chunks`: current behavior; return individual matching chunks.
- [x] `compact`: return the best chunks but group repeated evidence by conversation.
- [x] `conversations`: return one summarized result per conversation.

Acceptance behavior:

- [x] Repeated chunks from one conversation should not crowd out other relevant conversations.
- [x] Compact mode should still expose enough evidence for the agent to cite memory responsibly.
- [x] Existing callers should continue to get `chunks` by default.

## Phase 3: Add A Fact Extraction Store

- [x] Create a normalized fact model beside conversation storage:

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

- [x] `I have ...`
- [x] `I own ...`
- [x] `My favorite ... is ...`
- [x] `The creator is ...`
- [x] `<project> is ...`
- [x] `The command name is ...`
- [x] `The indexing strategy is ...`

Initial fact categories:

- [x] User owned items.
- [x] User preferences.
- [x] User identity/profile notes.
- [x] Project names and project attributes.
- [x] People associated with projects.
- [x] Recurring topics.

## Phase 4: Add Profile And Fact Tools

Add MCP tools after the fact model is stable:

- [x] `memory_fact_search`
- [x] `memory_profile_get`
- [x] `memory_fact_supersede`

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

1. [x] User says: "Actually, my Gibson is TV yellow, not cherry."
2. [x] The server creates a new fact.
3. [x] The old fact is marked with `superseded_by`.
4. [x] Future answers prefer the newer fact.
5. [x] If old and new facts both appear relevant, the answer mentions the correction.

Required behavior:

- [x] Superseded facts are excluded from normal profile answers.
- [x] Superseded facts remain available for audit and provenance.
- [x] Conflicting active facts produce a `conflict` answer basis.

## Phase 6: Answer Policy

Use facts first for direct profile questions:

- [x] "What guitar do I own?"
- [x] "What is my favorite command name?"
- [x] "Who created Velvet Lantern?"

Use conversation retrieval first for broader recall questions:

- [x] "What was my recent Codex conversation about?"
- [x] "What did we discuss about Velvet Lantern?"
- [x] "Summarize recent project ideas."

- [x] Use mixed mode when a direct fact answer needs conversation context.

## Acceptance Criteria

- [x] `memory_ask` can return `confidence`, `answer_basis`, and compact provenance.
- [x] Compact result mode prevents repeated chunks from one conversation dominating the response.
- [x] A direct fact question can be answered from the fact layer with source provenance.
- [x] User corrections can supersede older facts without deleting their source memories.
- [x] Conflicting facts produce a cautious answer with citations instead of a silent guess.
- [x] Existing MCP clients remain compatible with the current response fields.
