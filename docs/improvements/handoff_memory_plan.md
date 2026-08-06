# Handoff Memory Plan

## Goal

Make ai-memory-hub the reliable handoff layer between agent sessions and agent
clients. When one agent runs out of context, budget, time, or tool access, the
next agent should be able to continue from a compact, cited, permission-aware
handoff packet instead of rediscovering the task from raw chat history.

The product promise is simple: Agent A can save what matters, and Agent B can
resume the work with the goal, decisions, changed files, commands, blockers,
evidence, and next action intact.

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

Generic memory search helps, but it is not enough. Continuation needs a typed
handoff artifact with explicit status and provenance.

## Scope

- [ ] Add a first-class handoff memory type for cross-session and cross-agent
      task continuity.
- [ ] Preserve current MCP and HTTP memory surfaces while adding handoff-specific
      operations.
- [ ] Store handoffs as hub-owned records with links back to source
      conversations, files, commands, tests, and decisions.
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

## Phase 2: Stored Handoff Records

- [ ] Add metadata schema support for stored handoff records.
- [ ] Add `memory_handoff_create` over MCP.
- [ ] Add `memory_handoff_get` over MCP.
- [ ] Add `memory_handoff_update` over MCP.
- [ ] Add `memory_handoff_search` over MCP.
- [ ] Add matching HTTP endpoints:
      - [ ] `POST /memory/handoffs`
      - [ ] `GET /memory/handoffs/{id}`
      - [ ] `PATCH /memory/handoffs/{id}`
      - [ ] `POST /memory/handoffs/search`
- [ ] Support `supersedes_handoff_id` so later agents can update stale
      continuation packets without mutating history.

Acceptance criteria:

- [ ] API and MCP handoff response envelopes match existing hub conventions.
- [ ] Handoffs can be saved explicitly at the end of a session.
- [ ] Handoffs can be resumed explicitly at the start of a later session.
- [ ] Updates preserve an audit trail.

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

- [ ] Scope handoff reads by `owner_id`, project membership, and shared-memory
      policy.
- [ ] Require write permission for handoff creation and updates.
- [ ] Redact secrets from generated summaries and command output snippets.
- [ ] Preserve `metadata.save_intent` semantics for handoff records derived from
      memory inserts.
- [ ] Add review flow support for handoffs created from unmarked or
      client-auto-save material.
- [ ] Add audit events for create, read, update, supersede, and delete.

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

- [ ] Unit tests for handoff packet validation and redaction.
- [ ] Metadata-store contract tests for create, get, update, supersede, search,
      and authorization filters.
- [ ] MCP tool tests for handoff create/get/update/search.
- [ ] HTTP endpoint tests for handoff create/get/update/search.
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

- [ ] Should handoff packets be stored as a dedicated metadata table/collection
      or as typed conversation-adjacent records?
- [ ] Should generated handoffs require explicit user confirmation by default?
- [ ] What is the minimum useful handoff packet for very low token budgets?
- [ ] Should stale handoffs expire automatically or only be superseded?
- [ ] How should target-agent hints be represented without coupling the hub to
      specific vendors?
