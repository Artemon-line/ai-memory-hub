# Handoff Memory Plan

## Goal

Make ai-memory-hub the reliable handoff layer between agent sessions and agent
clients. When one agent runs out of context, budget, time, or tool access, the
next agent should be able to continue from a compact, cited, permission-aware
handoff packet instead of rediscovering the task from raw chat history.

The product promise is simple: a user can mark an ordinary memory as unfinished
work, then find and resume it tomorrow, next month, or after time away. A
generated handoff packet may summarize that memory, but it is a view rather than
a second stored resource.

If the hub is backed by reachable cloud storage or a hosted deployment, the same
handoff can be resumed from local machines, cloud IDEs, remote dev containers,
agent CLIs, and MCP-capable clients without copying a transcript between
environments.

## Why This Is Top Priority

Context loss is one of the most common failure modes in real agent workflows.
Users start meaningful work in Codex, opencode, Claude, Copilot, or another
client, then the session runs out of context or budget. The useful work is not
truly gone, but it is trapped in a conversation transcript that the next agent
cannot reliably reconstruct.

Generic memory search is the foundation. Continuation needs a small,
well-documented marker that lets normal search and ask focus on unfinished work.

## Scope

- [x] Represent a stored handoff as an ordinary memory with
      `metadata.handoff_at`.
- [x] Preserve the current MCP and HTTP memory surfaces; do not add a parallel
      handoff CRUD API.
- [x] Query unfinished work through normal `memory_search` and `memory_ask`
      using `handoff_only=true`.
- [ ] Make handoff packets compact enough for low-budget agents to consume.
- [ ] Make handoffs portable across local, LAN, hosted, and cloud development
      environments when the same authenticated hub is reachable.
- [ ] Keep handoff reads scoped by owner, project, shared-project membership,
      and existing auth policy.
- [ ] Keep A2A protocol support optional and later; implement the core handoff
      value over MCP and HTTP first.

## Non-Goals

- [ ] Do not replace conversation memory, facts, profile memory, or raw message
      storage.
- [ ] Do not let one agent hand off private project context to another agent
      without the same authorization checks used by search and ask.
- [ ] Do not treat retrieved handoff text as executable instructions.
- [ ] Do not expose secrets, raw tokens, DSNs, environment dumps, or private tool
      output in generated handoff packets.
- [ ] Do not require A2A before the handoff model is useful.

## Handoff Packet Model

Initial fields:

- [ ] `handoff_id`: hub-generated stable UUID.
- [ ] `project_id`: optional project/workspace scope.
- [ ] `thread_id`: optional source thread or upstream session identifier.
- [ ] `source_agent`: client or agent that created the handoff.
- [ ] `target_agent`: optional intended next agent or client.
- [ ] `goal`: concise user-level objective.
- [ ] `status`: `active`, `blocked`, `waiting_for_review`, `complete`, or
      `superseded`.
- [ ] `summary`: short continuation summary.
- [ ] `decisions`: ordered list of important decisions and rationale.
- [ ] `changed_files`: file paths, change intent, and whether changes were
      committed.
- [ ] `commands_run`: command, result, and important output summary.
- [ ] `validation`: tests, builds, checks, or manual verification already run.
- [ ] `blockers`: concrete blockers and what would unblock them.
- [ ] `next_steps`: ordered, actionable continuation steps.
- [ ] `citations`: source memory IDs, conversation IDs, chunks, or fact IDs.
- [ ] `created_at`, `updated_at`, `expires_at`: lifecycle timestamps.
- [ ] `confidence`: generated handoff confidence or completeness score.

Acceptance criteria:

- [ ] Handoff packets are compact, structured, and readable by humans and agents.
- [ ] Every generated packet links back to evidence instead of being an
      unsupported summary.
- [ ] A new agent can request "what was happening here?" and receive a useful
      continuation packet in one call.

## Phase 1: Retrieval-Only Handoff View

- [ ] Add internal handoff packet generation from existing conversations,
      summaries, facts, and recent project memory.
- [ ] Add deterministic summarization prompts/templates for continuation packets.
- [ ] Include explicit source citations and confidence notes.
- [ ] Add `result_mode="handoff"` or an equivalent read-only ask/search option
      if it can fit the existing response shape without breaking clients.
- [ ] Keep generated packets ephemeral until the user or agent explicitly saves
      them.

Acceptance criteria:

- [ ] No schema migration is required for the first read-only view.
- [ ] Existing `memory_ask` and search behavior remains backward compatible.
- [ ] Handoff generation refuses to include redacted or unauthorized memory.

## Phase 2: Stored Handoff Marker

- [x] Add optional `metadata.handoff_at` to the ordinary conversation schema.
- [x] Define presence of `handoff_at` as “unfinished work saved for later.”
- [x] Add `handoff_only` to existing HTTP and MCP search/ask requests.
- [x] Keep insert, retrieve, authorization, redaction, indexing, and storage on
      the normal memory path.
- [ ] Add a later resolution/link field only when a proven resume workflow
      requires it; do not introduce a parallel handoff lifecycle prematurely.

Acceptance criteria:

- [x] API and MCP responses remain the existing memory response envelopes.
- [ ] Handoffs can be saved explicitly at the end of a session.
- [ ] Handoffs can be resumed explicitly at the start of a later session.
- [x] Handoff memories inherit normal memory authorization and audit behavior.

## Phase 3: Agent Workflow Integration

- [ ] Add MCP prompt `create_handoff` for "save my current working state."
- [ ] Add MCP prompt `resume_handoff` for "continue this task."
- [ ] Add client-facing docs for Codex, opencode, Claude, Copilot, and other MCP
      clients.
- [ ] Add CLI commands:
      - [ ] `aim handoff create`
      - [ ] `aim handoff get`
      - [ ] `aim handoff search`
      - [ ] `aim handoff update`
- [ ] Add Connect UI snippets or setup guidance only after at least one real
      client flow is verified.

Acceptance criteria:

- [ ] A user can end a session with a saved handoff and begin another session
      with the same handoff.
- [ ] The recommended workflow does not require copying a full transcript.
- [ ] Real-client smoke coverage proves at least one MCP client can create and
      resume a handoff.

## Phase 4: Safety And Permission Model

- [x] Scope handoff reads by `owner_id`, project membership, and shared-memory
      policy.
- [x] Require write permission for handoff creation and updates.
- [x] Redact secrets from generated summaries and command output snippets.
- [ ] Preserve `metadata.save_intent` semantics for handoff records derived from
      memory inserts.
- [ ] Add review flow support for handoffs created from unmarked or
      client-auto-save material.
- [x] Add audit events for create, read, search, and supersede. Update/delete
      events remain tied to their future public mutation semantics.

Acceptance criteria:

- [ ] Agent B cannot retrieve a handoff unless it could retrieve the underlying
      memory.
- [ ] Generated handoffs do not leak raw secrets from logs, commands, config, or
      environment variables.
- [ ] Handoff records are evidence, not instructions; docs warn agents to treat
      them as context to verify.

## Phase 5: A2A Integration Path

- [ ] Track the current Agent2Agent protocol separately from the MCP tool
      surface.
- [ ] Add an optional A2A Agent Card only after the handoff contract is stable.
- [ ] Expose memory handoff capabilities as A2A tasks:
      - [ ] create a handoff
      - [ ] retrieve a handoff
      - [ ] search handoffs for a project
      - [ ] resume a handoff with citations
- [ ] Map A2A task IDs and agent IDs into handoff provenance.
- [ ] Keep MCP as the primary tool/data interface and A2A as an optional
      agent-to-agent coordination surface.

Acceptance criteria:

- [ ] The hub does not claim A2A support until an actual protocol-compatible
      server/card is implemented and tested.
- [ ] A2A support remains additive; MCP and HTTP users do not need it.
- [ ] A2A task provenance can be queried through normal memory retrieval.

## Phase 6: Graph-Aware Handoff Memory

- [ ] Link handoff records to projects, agents, files, commands, tests,
      decisions, blockers, source memories, and follow-up handoffs.
- [ ] Feed those links into the planned Neo4j graph mirror after provider parity
      is proven.
- [ ] Use bounded graph expansion to answer continuation questions:
      - [ ] "Who last worked on this?"
      - [ ] "What blocked the previous agent?"
      - [ ] "Which files changed before the session ended?"
      - [ ] "Which tests already passed?"
      - [ ] "What should I do next?"
- [ ] Keep graph-expanded handoff answers behind the same provenance and quality
      gates as other graph memory features.

Acceptance criteria:

- [ ] Handoff retrieval improves cross-agent continuation without changing raw
      memory semantics.
- [ ] Graph context is cited and bounded, not silently injected.
- [ ] Neo4j remains optional.

## Tests

- [x] Unit tests for handoff packet validation and redaction.
- [x] Unit tests for filtering ordinary memories by `handoff_at` presence.
- [x] MCP tests for `memory_search(..., handoff_only=true)`.
- [x] HTTP tests for `/memory/search` with `handoff_only=true`.
- [ ] Integration tests for Agent A creates handoff, Agent B resumes handoff.
- [ ] Negative tests for cross-user and cross-project handoff leakage.
- [ ] Regression tests for budget-constrained handoff packets.
- [ ] Bruno or real-client smoke coverage once the MCP surface exists.

## Documentation

- [ ] Update `README.md` to describe cross-agent task continuity after the first
      handoff surface ships.
- [ ] Update `docs/agents.md` with recommended create/resume workflows.
- [ ] Add examples for Codex-to-opencode and opencode-to-Codex handoffs.
- [ ] Document A2A as planned until protocol-compatible support exists.
- [ ] Document the difference between normal memory, facts, summaries, and
      handoff packets.

## Open Questions

- [x] Store unfinished handoffs as ordinary memories with a marker, not in a
      dedicated table or collection.
- [ ] Should generated handoffs require explicit user confirmation by default?
- [ ] What is the minimum useful handoff packet for very low token budgets?
- [ ] Should stale handoffs expire automatically or only be superseded?
- [ ] How should target-agent hints be represented without coupling the hub to
      specific vendors?
