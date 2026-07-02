# Plugin Readiness Plan

This plan tracks the work needed to make ai-memory-hub usable as a Codex-ready
plugin. The hub remains the local-first memory service; the plugin becomes the
installable wrapper for setup guidance, MCP wiring, skills, onboarding, and the
trust model users see inside Codex.

## Goal

Make ai-memory-hub reliable as a local Codex plugin, with a later path toward
broader distribution only after packaging, security, privacy, and documentation
criteria are satisfied.

Core user promise:

> Local-first memory for Codex and MCP-capable agents, so project decisions,
> architecture notes, prior conversations, and useful facts remain searchable
> across sessions.

Terminology follows `agents.md`: the Codex plugin captures explicitly selected
conversation or project context into the hub, retrieves memories from the hub,
and injects retrieved context into the active Codex task. The plugin does not own
memory storage or memory quality.

## Current State

Implemented:

- [x] Local-first HTTP and MCP memory service.
- [x] MCP tools for validate, insert, search, retrieve, ask, fact search,
  profile get, and fact supersession.
- [x] MCP resources and prompts documented for agent clients.
- [x] Explicit save-intent policy and review-pending memory behavior.
- [x] Bearer-token and OAuth resource-server auth modes.
- [x] User and project isolation for authenticated memory operations.
- [x] Storage-provider health, fallback policy, and degraded-mode behavior.
- [x] Real-client and contract-oriented MCP smoke planning.
- [x] Plugin readiness plan added to project docs.

Missing or partial:

- [ ] Codex plugin wrapper with `.codex-plugin/plugin.json`.
- [ ] Personal marketplace entry for local installation.
- [ ] Plugin-specific first-run docs.
- [ ] Codex-facing skill prompts for common memory workflows.
- [ ] Plugin-scoped MCP configuration and health-check helper.
- [ ] Plugin listing metadata, icon, screenshots, and privacy note.
- [ ] Clean install/update flow verified from a fresh environment.
- [ ] Dedicated plugin security and privacy review.

## Scope

In scope:

- Local Codex plugin packaging for ai-memory-hub.
- A first plugin scaffold in `codex-plugin/` inside this repository.
- Personal marketplace installation flow.
- MCP server registration or documented MCP connection.
- Plugin-specific onboarding and troubleshooting.
- Privacy, permission, authentication, and data-handling guidance.
- Local smoke tests for plugin install, service health, and MCP usage.

Out of scope for the first plugin iteration:

- Official/public marketplace publication.
- Hosted multi-tenant operations.
- Cloud sync.
- Automatic transcript capture without explicit user opt-in.
- Admin web UI.

## P0: Product Positioning And User Value

- [ ] Define the plugin as a memory layer for agents, not as a generic vector
  database demo.
- [ ] Add a one-paragraph value statement to plugin docs and listing metadata.
- [ ] Document the primary workflows:
  - capture an important Codex conversation with explicit user intent
  - retrieve previous project decisions
  - ask over remembered architecture notes and debugging history
  - inject retrieved context into the active Codex task
  - retrieve cited memories for inspection
  - share one local memory backend across MCP-capable clients
- [ ] State clearly that ai-memory-hub does not capture every conversation
  automatically.
- [ ] Link plugin docs back to `agents.md` for the full MCP/agent contract.

Done when:

- A new user can tell what problem the plugin solves from the listing and first
  docs screen.
- Docs do not imply background transcript capture or hidden persistence.

## P0: Local Plugin Packaging

- [ ] Create a plugin wrapper in `codex-plugin/`, separate from the service
  implementation.
- [ ] Add `codex-plugin/.codex-plugin/plugin.json`.
- [ ] Add a local personal marketplace entry.
- [ ] Choose plugin category:
  - `Productivity`
  - `Developer Tools`
- [ ] Add short description:
  `Local-first memory for Codex and MCP agents`.
- [ ] Set authentication policy to match the setup flow.
- [ ] Add optional `skills/` only when the workflow prompts are implemented.
- [ ] Add optional `.mcp.json` only when the plugin owns MCP server
  registration.
- [ ] Add optional `assets/` for icon and listing media.
- [ ] Add optional `scripts/` for setup, health checks, and update helpers.
- [ ] Keep the core service code in `memory/`; do not duplicate API, MCP,
  storage, or ingestion implementation inside the plugin wrapper.
- [ ] Revisit a separate small plugin repository only after the local install
  and update flow is stable.
- [ ] Validate the plugin with the Codex plugin validator.

Done when:

- The plugin appears in the local Codex Plugins list from a personal marketplace.
- The plugin wrapper does not duplicate the ai-memory-hub service code.
- Plugin metadata is accurate for the current local-first behavior.

## Repository Layout Decision

Start with the plugin scaffold inside this repository:

```text
codex-plugin/
  .codex-plugin/
    plugin.json
  skills/
  scripts/
  assets/
```

Rationale:

- The plugin can evolve with the service while the interface is still changing.
- Documentation, MCP contracts, auth behavior, and setup scripts stay close to
  the implementation they describe.
- The wrapper remains visibly separate from `memory/`, so ai-memory-hub continues
  to be usable by non-Codex MCP clients.
- A future marketplace-oriented split can copy the stabilized wrapper into a
  smaller distribution repository with a narrower review surface.

## P0: First-Run Installation Flow

- [ ] Pick one recommended local install path before documenting alternatives.
- [ ] Document prerequisites:
  - Python and `uv`
  - Docker or Podman only when using container setup
  - available local port
  - storage provider requirements
- [ ] Document how to start ai-memory-hub from the repository.
- [ ] Document how to start ai-memory-hub from a container.
- [ ] Document how Codex connects to the MCP endpoint.
- [ ] Add a health-check command or script.
- [ ] Add a first memory save example.
- [ ] Add a first search example.
- [ ] Add a first ask-over-memory example.
- [ ] Add troubleshooting for service startup, port conflicts, config errors,
  storage failures, and auth failures.

Done when:

- A clean local environment can install, start, connect, and verify the plugin
  without guessing.
- Setup errors point to a clear next action.

## P0: Data Handling And Privacy

- [x] Require or support explicit save intent for intentional writes.
- [x] Support review-pending memory status for untrusted or ambiguous writes.
- [x] Redact internal content hashes from external memory responses.
- [x] Avoid raw token logging in auth-protected paths.
- [ ] Add plugin-facing privacy note.
- [ ] Document where metadata and vectors are stored for the recommended setup.
- [ ] Document which actions write memory.
- [ ] Document delete, reject, approve, and export behavior.
- [ ] Document project, thread, source, and user scoping.
- [ ] Document what should not be stored:
  - credentials
  - personal data that does not belong in project memory
  - raw debug logs
  - tool output unless explicitly requested
- [ ] Add a plugin onboarding warning before LAN or public exposure guidance.

Done when:

- Users can understand what is stored, where it is stored, and how to avoid
  accidental persistence.
- Privacy guidance appears before any non-loopback deployment instructions.

## P0: Authentication And Exposure Safety

- [x] Support `api.auth: none` for loopback-only local testing and CI.
- [x] Support `api.auth: bearer_token` for local or trusted-LAN deployments.
- [x] Support `api.auth: oauth_resource_server` for future federated MCP
  deployments.
- [x] Protect `/memory/*` and `/mcp/*` when auth is enabled.
- [x] Scope authenticated memory operations to the user.
- [ ] Make the plugin setup default to loopback-only exposure.
- [ ] Warn or fail when `auth: none` is combined with non-loopback bind.
- [ ] Add token setup guidance to the plugin first-run docs.
- [ ] Document the minimum safe auth mode for each deployment shape.
- [ ] Verify plugin docs never put tokens in query strings, logs, or examples.

Done when:

- Local setup remains simple.
- LAN or public exposure cannot be followed from docs without seeing auth
  guidance first.
- Tests prove one user cannot search, retrieve, ask over, or mutate another
  user's memory.

## P0: Permission Boundaries

- [x] Document agent rules for validate-before-insert.
- [x] Document save-intent expectations in `agents.md`.
- [x] Protect project-scoped memory operations with membership checks.
- [ ] Add plugin-specific permission language for read, write, and admin
  capabilities.
- [ ] Keep write actions behind explicit save intent, user confirmation, or
  configured client auto-save.
- [ ] Keep admin/debug operations disabled or undocumented until intentionally
  exposed.
- [ ] Ensure plugin skills do not encourage storing debug logs or operational
  planning text by default.
- [ ] Ensure plugin docs say clients must not write directly to backing
  databases or vector stores.

Done when:

- A user can inspect the plugin docs and understand what Codex may read or
  write through ai-memory-hub.
- MCP tool schemas, HTTP routes, and docs describe the same permission model.

## P0: Reliable MCP Plugin Behavior

- [x] Expose `memory_validate`.
- [x] Expose `memory_insert`.
- [x] Expose `memory_search`.
- [x] Expose `memory_retrieve`.
- [x] Expose `memory_ask`.
- [x] Expose fact/profile tools.
- [x] Expose useful MCP resources and prompts.
- [x] Use stable MCP response envelopes documented in `agents.md`.
- [ ] Add plugin-specific smoke instructions for Codex.
- [ ] Add a plugin health-check flow that verifies the MCP endpoint.
- [ ] Verify auth failures return actionable plugin setup errors.
- [ ] Verify storage failures return actionable plugin setup errors.
- [ ] Verify degraded health is visible to the user before writes are trusted.
- [ ] Verify pagination and token budgeting in the plugin happy path.

Done when:

- Codex can connect to the plugin-backed MCP server and complete save, search,
  retrieve, and ask workflows.
- Failures are clear enough for a user to fix config, auth, or service state.

## P1: Codex-Facing Skills

- [ ] Add a skill for saving the current conversation intentionally.
- [ ] Add a skill for searching remembered project context before code changes.
- [ ] Add a skill for asking over project memory with citations.
- [ ] Add a skill for injecting retrieved project context into the active task.
- [ ] Add a skill for reviewing pending memories if review-pending mode is
  enabled.
- [ ] Keep skill instructions aligned with `agents.md`.
- [ ] Include save-intent metadata in all write-oriented skill guidance.
- [ ] Avoid automatic saving unless a user explicitly enables it.

Done when:

- Codex can use the plugin through named workflows without the user needing to
  remember raw MCP tool details.
- Skills do not bypass save-intent, auth, or project-scoping rules.

## P1: Documentation And Onboarding

- [ ] Add `docs/plugin_overview.md` or a plugin section in existing docs.
- [ ] Add install/start/connect/test steps.
- [ ] Add secure-localhost-first guidance.
- [ ] Add LAN deployment guidance that requires auth.
- [ ] Add troubleshooting for:
  - plugin not visible
  - MCP endpoint unavailable
  - auth rejected
  - storage provider unavailable
  - embeddings unavailable
  - degraded fallback active
- [ ] Link to `mcp_plan.md`, `mcp_client_smoke_plan.md`, and
  `bearer_api_key_auth_plan.md` for deep details.
- [ ] Keep examples in bash style.

Done when:

- A new user can complete the happy path in under five minutes from a clean
  checkout.
- Deep architecture details are linked instead of repeated in the plugin docs.

## P1: Listing Polish

- [ ] Add display name: `ai-memory-hub`.
- [ ] Add short description: `Local-first memory for Codex and MCP agents`.
- [ ] Add longer description:
  `Search, retrieve, and ask over project memories, architecture decisions, and
  saved conversations through a local MCP memory service.`
- [ ] Add privacy note:
  `Stores memories locally by default. Do not expose the service beyond
  localhost without authentication.`
- [ ] Add setup requirements.
- [ ] Add icon.
- [ ] Add optional screenshot of save/search/ask workflow.
- [ ] Add optional diagram showing Codex, MCP, ai-memory-hub, metadata storage,
  and vector storage.

Done when:

- The listing is accurate, concise, and does not overclaim official marketplace
  support.
- Privacy and auth expectations are visible before first use.

## P1: Security And Privacy Review

- [ ] Review plugin manifest for overbroad claims or permissions.
- [ ] Review setup scripts for secret-safe output.
- [ ] Confirm docs never log or display bearer tokens outside intentional token
  creation output.
- [ ] Confirm MCP errors do not leak sensitive backend details.
- [ ] Confirm unsafe bind/auth combinations are warned, blocked, or documented.
- [ ] Confirm deletion, rejection, approval, and export behavior are documented.
- [ ] Confirm save-intent behavior is represented accurately in all plugin
  workflows.
- [ ] Record review results before sharing the plugin beyond local use.

Done when:

- High-risk findings are fixed or explicitly scoped out with rationale.
- The plugin has a minimal privacy note suitable for listing metadata.

## P2: Shareable Plugin Candidate

- [ ] Validate install and update from a fresh local environment.
- [ ] Run Codex MCP smoke tests through the plugin setup.
- [ ] Run at least one non-Codex MCP client smoke test against the same service.
- [ ] Add release notes for plugin wrapper changes.
- [ ] Decide whether the plugin should remain personal/local or pursue broader
  marketplace distribution.
- [ ] If pursuing broader distribution, document additional review requirements:
  - support model
  - versioning
  - security response
  - privacy statement
  - data retention story
  - hosted-service boundaries, if any

Done when:

- The plugin is safe to share with another developer for local testing.
- Broader distribution is treated as a separate approval and hardening track.

## Testing

- [ ] Plugin manifest validation passes.
- [ ] Personal marketplace entry renders in Codex.
- [ ] Service health check passes from the documented setup.
- [ ] MCP initialize and tools/list pass from Codex.
- [ ] Save-first-memory workflow passes.
- [ ] Search workflow passes.
- [ ] Retrieve workflow passes.
- [ ] Ask-over-memory workflow passes with citations.
- [ ] Missing service produces an actionable setup error.
- [ ] Invalid token produces an actionable auth error without leaking token
  material.
- [ ] Non-loopback `auth: none` exposure is warned, blocked, or explicitly
  flagged by setup checks.
- [ ] Docs build passes after plugin docs are added.

## Acceptance Criteria

- [ ] ai-memory-hub can be installed as a local Codex plugin.
- [ ] The plugin can connect Codex to the ai-memory-hub MCP server.
- [ ] A user can capture, search, retrieve, ask over memory, and inject retrieved
  context from the documented first-run flow.
- [ ] The docs explain local storage, save intent, auth, and exposure safety.
- [ ] The plugin does not encourage automatic memory capture by default.
- [ ] The plugin listing includes accurate setup and privacy notes.
- [ ] Plugin validation and docs validation pass.
- [ ] Security/privacy review is complete before broader sharing.

## Related Docs

- Agent integration: `agents.md`
- MCP plan: `mcp_plan.md`
- MCP client smoke plan: `mcp_client_smoke_plan.md`
- MCP authorization compliance plan: `bearer_api_key_auth_plan.md`
- Explicit memory save intent plan: `improvements/explicit_memory_save_intent_plan.md`
- First release readiness plan: `first_release_readiness_plan.md`
