# Client Feedback Improvement Plan

Source date: `15-06-2026`

Status: partial

## Goal

Turn real opencode and Codex MCP usage feedback into a concrete improvement backlog that makes ai-memory-hub easier for agents to use, easier for users to trust, and less surprising at the response-schema level.

## Feedback Summary

Observed positives:

- opencode found the MCP straightforward because the tools are simple and the schemas are predictable.
- Codex found fact answers easy to use because normalized facts returned citations and confidence.
- Both clients could use the core insert/search/profile/ask flow without a new integration layer.

Observed improvement themes:

- Nested conversation JSON is easy enough, but clients still need guidance to structure messages and tags correctly.
- `memory_ask` should avoid surprising shapes such as a successful fact answer with `results: []`.
- Fact answers need cleaner user-facing text while preserving raw stored memory.
- Confidence should explain why it is high, medium, low, or absent.
- Personal facts need freshness fields such as creation time, update time, and last confirmation.
- Stored facts need light normalization for spelling/casing without destroying provenance.
- Auto-tagging, conversation threading, richer filters, and summaries would reduce manual work.
- Client-provided `metadata.summary` is a lightweight retrieval hint, not graph memory. It should supplement the full saved conversation and never replace raw messages.
- Bulk insert is intentionally not planned now because batched conversation
  boundaries can introduce duplicated, split, or out-of-sync memory. Clients
  should save one complete conversation per insert.
- General delete/update tools are intentionally not part of the agent-facing MCP surface. Administrative memory hiding or cleanup should be CLI/API-only and gated by local/admin controls.
- Auth and per-user isolation are required before LAN, Raspberry Pi, multi-user,
  or admin UI deployments are recommended.
- Shared project workspaces should extend bearer-token isolation without
  replacing `owner_id`: the actor remains server-stamped, while `project_id`
  becomes the memory collaboration boundary.

## P0: Response Shape Clarity

Source feedback:

- Codex saw `results: []` even though the answer came from the fact layer.
- Codex wanted raw facts and polished answers separated.

Implementation sequence:

- [x] Make `memory_ask` return structured evidence for fact-backed successful answer paths:
  - [x] fact evidence for `fact_layer`
  - [x] conflicting fact evidence for `conflict`
  - [x] fact evidence plus retrieved chunks for `mixed`
  - [x] general `evidence` entries for pure `direct_memory` chunk answers
- [x] Keep `answer` as the user-facing polished string.
- [x] Add stable `evidence` and `structured_evidence.facts` sections for normalized fact-layer evidence instead of overloading chunk-shaped `results`.
- [x] Document the exact relationship between `answer`, `results`, `citations`, `provenance`, and fact evidence in public API docs.
- [x] Add tests for fact-only answers, mixed answers, and conflict answers.
- [x] Add explicit no-hit response-shape tests for `structured_evidence`.

Acceptance criteria:

- A fact-layer answer no longer looks like a miss to clients that inspect structured fields.
- Raw stored text, normalized facts, citations, and polished answer text are distinguishable.
- Existing clients that read `answer`, `status`, and `confidence` continue to work.

## P0: Fact Freshness And Source Quality

Source feedback:

- Codex wanted to know why confidence is high, such as direct user statement versus inference.
- Codex wanted freshness fields for personal facts.

Implementation sequence:

- [x] Add `source_quality` and `confidence_reason` to fact and ask responses.
- [x] Start with deterministic values:
  - [x] `direct_user_statement`
  - [x] `assistant_statement`
  - [x] `inferred_from_conversation` for recurring-topic facts
  - [x] `corrected_by_user`
  - [x] conflict answers expose a confidence reason for disagreeing active facts
- [x] Add or expose fact freshness fields:
  - [x] `created_at`
  - [x] `updated_at`
  - [x] `last_confirmed_at`
  - [x] `superseded_at` where applicable
- [x] Update correction/supersession flows so direct user confirmations refresh `last_confirmed_at`.
- [x] Add API and MCP tests for freshness and source-quality fields.
- [x] Add SQLite and Postgres storage-level tests for persisted freshness/source-quality fields.

Acceptance criteria:

- A client can explain why confidence is high without reading raw conversation chunks.
- A user can tell whether a personal fact is recent, stale, corrected, or superseded.

## P1: Fact Text Normalization And Answer Polish

Source feedback:

- Codex saw misspelled or awkward answer text such as `aniversary`.
- Codex asked for spelling/casing normalization or separate raw and polished fields.

Implementation sequence:

- [x] Preserve raw source text exactly in provenance and source conversations.
- [x] Add normalized fact fields for display and matching:
  - [x] `object_raw`
  - [x] `object_normalized`
  - [x] optional structured qualifiers for dates, names, casing, and common spelling fixes
- [x] Use normalized fact values for answer wording when confidence is high.
- [x] Keep normalization deterministic at first; add hosted or local LLM cleanup only behind explicit configuration.
- [x] Add regression tests for common spelling/casing cleanup and no-overwrite provenance behavior.

Acceptance criteria:

- User-facing answers are readable and consistently cased.
- The system can still cite and audit the original stored text.
- Normalization never silently changes source conversation payloads.

## P1: Auto-Tagging

Source feedback:

- opencode suggested inferring tags instead of requiring manual tags.

Implementation sequence:

- [ ] Generate deterministic tags from extracted topics, entities, source, and fact predicates.
- [ ] Store generated tags separately from user-supplied tags:
  - [ ] `tags`
  - [ ] `auto_tags`
  - [ ] `tag_sources`
- [ ] Use auto-tags for retrieval reranking only after tests show no precision regression.
- [ ] Expose auto-tags in search/retrieve responses and CLI output.
- [ ] Add tests for generated tags, manual tag preservation, and metadata-aware reranking.

Acceptance criteria:

- Users and agents can omit tags for common conversations and still get useful metadata.
- Manual tags remain authoritative and are not overwritten by generated tags.

## P1: Conversation Threading

Source feedback:

- opencode suggested linking related conversations or continuing a thread over time.

Implementation sequence:

- [ ] Promote existing upstream-thread metadata into a documented cross-client thread model.
- [ ] Add `thread_id`, `parent_conversation_id`, and `related_conversation_ids` where storage adapters can support them.
- [ ] Add append-or-continue behavior for clients that send a stable upstream thread id.
- [ ] Add search filters and result grouping by thread.
- [ ] Add tests for cross-client handoff, append-only updates, and thread-aware retrieval.

Acceptance criteria:

- A user can ask about a project or topic across multiple Codex/opencode conversations and receive thread-aware context.
- Threading does not collapse unrelated conversations just because they share broad tags.

## P0: Bearer Auth And Per-User Isolation

Source feedback:

- A persistent AMH service on a Raspberry Pi or LAN would expose conversation
  history without enforced auth.
- Multiple clients or users need separate memory spaces, not just one shared
  token-protected database.
- A future admin UI needs a clear auth boundary before it can safely manage
  memory and keys.

Current status:

- `api.auth: bearer_token` is enforced for `/memory/*` and `/mcp/*`.
- MCP and HTTP API are mounted by the same FastAPI app.
- Conversation and fact reads are scoped by server-derived `owner_id` when
  bearer auth is enabled.

Implementation sequence:

- [x] Add `api.auth: bearer_token` for simple personal access tokens.
- [x] Keep `Authorization: Bearer <token>` as the only token transport so the
  client shape remains compatible with MCP OAuth later.
- [x] Store users and token hashes in the metadata database, not config files.
- [x] Add admin CLI commands to create users, issue tokens, list tokens, and
  revoke tokens.
- [x] Map each token to `user_id`; scopes remain planned.
- [x] Stamp new conversations and facts with server-side `owner_id`.
- [x] Scope search, retrieve, ask, fact search, profile, and future admin actions
  by `owner_id`.
- [x] Do not trust client-supplied owner metadata.
- [x] Filter vector candidates through metadata ownership before returning or
  answering.
- [x] Redact bearer tokens from logs and diagnostics.
- [x] Keep Google/Apple/Meta OAuth as a later `oauth_resource_server` or reverse
  proxy option.

Acceptance criteria:

- LAN deployments can require a bearer token for `/memory/*` and `/mcp/*`.
- Each bearer token maps to exactly one user/principal.
- User A cannot read, search, retrieve, or ask over User B's memory.
- Tokens are shown once at creation and only hashes are stored.
- `auth=none` remains available for CI and loopback-only local testing.

## P0: Project Workspaces And Shared Collaboration

Source feedback:

- Per-user isolation protects private memory, but collaboration needs an
  explicit shared workspace.
- A user may belong to multiple projects and choose when to save a conversation
  to a shared project instead of their private memory.
- Shared memory should not blur audit identity: AMH still needs to know which
  user inserted or corrected each conversation or fact.

Current status:

- Bearer auth maps tokens to server-side users.
- Conversations and facts are already stamped with `owner_id`.
- Shared project membership is implemented in SQLite and Postgres metadata
  stores, with project-scoped reads/writes in HTTP and MCP memory operations.
- Admin CLI commands can create/list projects and add/list project members.

Implementation sequence:

- [x] Add `projects` and `project_memberships` to the metadata stores.
- [x] Auto-create a private default project for every user.
- [x] Add `project_id` to conversations, facts, and vector metadata.
- [x] Treat omitted `project_id` as the authenticated user's private default
  project.
- [x] Keep `owner_id` as the actor/audit field and never trust client-supplied
  owner metadata.
- [x] Validate project membership before insert, search, retrieve, ask, fact
  search, profile lookup, and fact supersession.
- [x] Add optional `project_id` to HTTP and MCP tool schemas.
- [x] Keep project creation, membership, and role changes in admin CLI/API flows
  before exposing any agent-facing MCP project administration.

Acceptance criteria:

- Existing users get private memory by default.
- Jane and Carl can both access shared project `321` when both have membership.
- Jane cannot access Carl's private project or unrelated project `234`.
- Duplicate conversation hashes dedupe inside one project, not globally.

## P2: Admin-Only Mutation Workflows

Source feedback:

- opencode wanted delete/update in addition to supersession.
- Project decision: general mutation over MCP is not worth the security and
  auditability risk. Source conversations should remain immutable by default, and
  correction should use fact supersession. Any future mutation support must be
  administrative only.

Implementation sequence:

- [ ] Define admin-only mutation semantics before adding any endpoint or CLI command:
  - [ ] soft delete conversation
  - [ ] soft delete fact
  - [ ] edit metadata only
  - [ ] append messages
  - [ ] supersede fact
- [ ] Keep source conversation payloads immutable by default.
- [ ] Do not add MCP delete/update tools.
- [ ] Add admin-scoped HTTP API endpoints only after auth scope handling is clear.
- [ ] Add admin CLI commands for local maintenance only after API semantics are stable.
- [ ] Add audit fields for who/what performed a mutation.
- [ ] Add provider contract tests for metadata and vector cleanup parity.

Acceptance criteria:

- Users can hide incorrect memory through explicit admin flows without losing auditability by default.
- Vector and metadata stores remain consistent after delete/update operations.
- Agent clients cannot mutate or delete memory through MCP.

## P0: Advanced Search Filters

Source feedback:

- opencode wanted filters by date range, tags, and source.

Current status:

- Source, date range, and tag filters exist for `memory_search` and `memory_ask`.
- Fact/profile review supports source, predicate, date range, confidence,
  active/superseded/all status, source quality, and freshness filters.

Implementation sequence:

- [x] Document current filters in MCP tool descriptions and initialize instructions.
- [x] Add filter support to `memory_ask` where it can preserve answer quality.
- [x] Add fact/profile filters for source, predicate, date range, confidence, active/superseded status, and freshness.
- [x] Add tests for `memory_ask` filtered retrieval and fact/profile filter combinations.

Acceptance criteria:

- Agents can narrow retrieval without post-filtering client-side.

## P0: Conversation Summary Metadata And Profile Views

Source feedback:

- Users want exact data to be easier to find during search.
- Clients can cheaply produce a short summary at save time, but they can also
  speculate. The summary must therefore be treated as metadata, not source truth.
- opencode wanted a built-in way to get a profile summary without fetching raw
  chunks.

Current status:

- Full `messages` are the source of truth.
- `metadata.tags` and inferred topics already help retrieval/reranking.
- `metadata.summary` is documented as a first-class ingestion hint.
- `memory_profile_get` returns a compact generated profile summary built from
  active facts, freshness fields, source-quality counts, and fact provenance.
- Generated summaries are stored separately from raw chunks and normalized facts
  in provenance-aware summary records.
- Built-in generated conversation, topic, and project summaries are implemented
  with provenance-aware generated summary records.

Implementation sequence:

- [x] Document optional `metadata.summary` in the conversation schema and public docs.
- [x] Update MCP save guidance to ask clients for a short factual summary while
  still saving the complete conversation.
- [x] Validate `metadata.summary` as a bounded string.
- [x] Include summary text in metadata search/reranking so whole-conversation
  themes can help find exact chunks.
- [x] Return summary in search/retrieve responses as metadata, not as a citation
  unless the raw message evidence also supports the answer.
- [x] Add tests showing summary improves recall without replacing message
  provenance.
- [x] Add server-generated per-conversation summaries after client-provided
  summaries have enough real usage examples.
- [x] Add a profile summary response that combines active facts, freshness, and
  compact provenance.
- [x] Add topic and project summaries after conversation summaries are reliable.
- [x] Keep generated summary storage separate from raw chunks and normalized facts.
- [x] Add tests showing generated summaries cite their source conversations or facts.

Acceptance criteria:

- Clients can provide a concise conversation summary during save.
- Search can use the summary to find relevant conversations more reliably.
- Answers still cite raw messages or normalized facts, not unsupported summary
  speculation.
- Agents can fetch compact profile summaries without reading every raw chunk.
- Agents can fetch compact project summaries once project summaries are implemented.

## P0: Bruno Integration Test Layer

Source feedback:

- Local MCP/API debugging benefits from a runnable black-box test collection
  that contributors can execute without writing Python.
- CI failures are easier to inspect when public API/MCP smoke tests produce a
  human-readable report artifact.

Decision:

- Add Bruno as a local and CI integration layer for a running ai-memory-hub
  server. Initial unauthenticated health, API, and MCP smoke coverage is
  implemented under `tests/bruno`.
- Keep Bruno focused on public API/MCP contract coverage and persistence through
  the configured DB/vector stores.
- Keep pytest as the source of truth for detailed validation, storage adapter
  behavior, auth edge cases, and ranking internals.

Plan:

- Track remaining work in `../bruno_integration_test_plan.md`.
- Start with unauthenticated health, API memory flow, and MCP memory flow.
  Done.
- Add bearer-token/project smoke coverage after the base collection is stable.
  Done.
- Add an OAuth resource-server protected-resource metadata guard.
  Done.
- Add filter smoke coverage after the base collection is stable.
  Done.
- Run Bruno against the Postgres/PGVector stack locally and in CI.
  Done for the initial workflow.
- Verify the unauthenticated smoke collection with the real Bruno CLI locally.
  Done.

## Closed: Bulk Conversation Insert

Source feedback:

- opencode wanted bulk conversation insert.

Decision:

- Single conversation insert is implemented through HTTP, MCP, and CLI.
- Do not add a first-class bulk insert endpoint, CLI command, or MCP tool now.
- Clients should store each whole conversation as one `conversation_json` object
  with a complete `messages` list, not split one thread into many batch items.
- Importers that have many conversations should loop over the existing single
  insert path and preserve each source conversation/thread boundary explicitly.
- This keeps validation, dedupe, hashing, fact extraction, trusted append, and
  vector indexing synchronized through one existing write path.

Rationale:

- Bulk writes make it easier for clients to split one real thread into many fake
  conversations, merge unrelated sessions, or resend overlapping messages.
- Partial batch success/error handling adds complexity without solving a current
  product need.
- One complete conversation per insert keeps provenance and facts easier to
  audit.

Acceptance criteria:

- MCP and docs guide clients to save whole conversations, not arbitrary batches.
- Future importer work can still iterate single inserts for many independent
  source conversations.

## Priority Mapping

- P0: bearer auth/user isolation, project workspaces, response shape clarity, fact freshness, source-quality explanation, advanced filters, and conversation summary metadata/profile views.
- P1: fact text normalization, answer polish, auto-tagging, and conversation threading.
- P2: admin-only mutation workflows and generated topic/project summaries.

## Done When

- The highest-priority feedback items are represented as tracked roadmap work.
- The MCP response contract is clearer for both chunk-backed and fact-backed answers.
- Fact answers expose source quality, freshness, raw values, and polished values without breaking existing clients.
- Manual tagging and single-conversation insert remain supported, but agents can rely on auto-tagging and batch workflows where available.
