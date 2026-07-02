# Project Workspace Collaboration Plan

## Goal

Extend bearer-token user isolation with shared project workspaces. A user should
have a private default memory space, while also being able to participate in one
or more shared projects where multiple users can save, search, ask, and correct
memory together.

Example:

- Jane belongs to projects `123` and `321`.
- Carl belongs to projects `234` and `321`.
- Jane and Carl can both use project `321` as a shared memory workspace.
- Jane's private default project remains invisible to Carl.

## Core Model

Do not replace `owner_id` with `project_id`. They answer different questions:

- `owner_id`: who authenticated and performed the action.
- `project_id`: which memory workspace the conversation or fact belongs to.

The storage model should add:

```text
projects
  id
  owner_id
  name
  description
  is_default
  created_at
  updated_at
  archived_at

project_memberships
  project_id
  user_id
  role
  created_at
  updated_at
```

Memory-bearing records should be project-scoped:

```text
conversations.project_id
facts.project_id
```

Keep server-stamped actor fields for provenance and audit:

```text
conversations.owner_id
facts.owner_id
```

Vector rows should include `project_id` metadata where the provider supports it,
but authorization must still be enforced after metadata hydration so provider
filter differences cannot cause cross-project leakage.

## Default Private Project

Every user should have exactly one default private project. When a client omits
`project_id`, the server resolves the request to the authenticated user's
default private project.

This keeps existing single-user behavior intuitive:

- Local users can keep saving memory without thinking about projects.
- LAN or multi-user deployments get private isolation by default.
- Shared collaboration requires an explicit project choice.

When bearer auth is disabled, the server can use a synthetic local default
project so loopback-only development remains simple.

## Authorization Rules

Every protected read or write should follow this sequence:

1. Authenticate the bearer token to a server-side user.
2. Resolve the effective `project_id` from the request, or use the user's
   default private project.
3. Verify the user has a membership role for that project.
4. Run the operation scoped to the effective project.

Do not trust client-supplied `owner_id`. The server continues to stamp
`owner_id` from the authenticated principal. Client-supplied `project_id` is only
accepted after membership validation.

Initial roles should stay small:

- `admin`: manage project metadata and members; read and write memory.
- `writer`: insert conversations, supersede facts, and read memory.
- `reader`: search, retrieve, ask, and read facts/profile summaries.

Avoid fine-grained scopes until real client usage shows a concrete need.

## API And MCP Contract

Add optional `project_id` to user-facing memory operations:

- `memory_insert`
- `memory_search`
- `memory_retrieve`
- `memory_ask`
- `memory_fact_search`
- `memory_profile_get`
- `memory_fact_supersede`

If `project_id` is omitted, use the authenticated user's default private
project. If it is provided, validate project membership before reading or
writing.

Useful helper operations:

- `memory_project_list`: list projects visible to the authenticated user.
- `memory_project_get`: fetch one visible project.
- `memory_project_default_get`: return the user's default private project.

Keep member management out of MCP at first. Project creation, membership
changes, token issuance, revocation, and role changes should be admin CLI/API
workflows until audit behavior and operator UX are stronger.

## Retrieval And Dedupe Behavior

Project membership becomes the collaboration boundary. Search, retrieve, ask,
fact search, profile lookup, and fact supersession should filter by the effective
project, not only by `owner_id`.

Conversation dedupe should be project-scoped:

```text
unique(project_id, conversation_hash)
```

This allows the same source conversation to exist independently in Jane's
private project and in shared project `321`, while duplicate inserts inside the
same shared project continue to dedupe.

For vector search:

1. Search the vector provider.
2. Hydrate candidates through metadata.
3. Drop candidates outside the effective project before ranking or answering.
4. Add provider-side `project_id` filters later as an optimization, not as the
   only authorization control.

## Fact And Profile Semantics

Facts are scoped to a project. This prevents personal profile facts from leaking
into team memory and prevents shared project facts from polluting private memory.

Examples:

- Jane's private project can contain `Jane owns a Gibson Special`.
- Shared project `321` can contain `Velvet Lantern uses PGVector`.

Questions about the user, such as "what guitar do I own?", should usually use
the user's default private project unless the client explicitly selects another
project. Questions about a shared work item should use the active shared
project.

Profile views should therefore be project-relative:

- private profile for a user's default project
- shared project profile/facts for a collaboration project

## Implementation Sequence

- [x] Add metadata-store tables for `projects` and `project_memberships`.
- [x] Auto-create one default private project for each user.
- [x] Add `project_id` to conversations and facts in SQLite and Postgres.
- [x] Migrate existing authenticated rows into each owner's default private
      project.
- [x] Add project-scoped uniqueness for conversation hashes.
- [x] Add project membership checks to ingestion, search, retrieve, ask, fact
      search, profile lookup, and fact supersession.
- [x] Add optional `project_id` to HTTP and MCP tool schemas.
- [x] Add HTTP/MCP project list/default helper operations.
- [x] Add admin CLI flows for project creation and membership management.
- [ ] Add admin API flows for project creation and membership management if a
      non-local admin UI needs them.
- [x] Add vector metadata `project_id` where providers support metadata filters.
- [x] Add tests proving private default isolation, shared project collaboration,
      reader/writer/admin role behavior, and vector candidate filtering.

## Migration Notes

Existing bearer-auth data has `owner_id` but no `project_id`. Migration should:

1. Create a default private project for each existing user.
2. Assign each user's conversations and facts to that user's default project.
3. Preserve `owner_id` unchanged for provenance.
4. Preserve existing unauthenticated development data under a synthetic local
   default project.

Migration should be idempotent so SQLite and Postgres startup initialization can
run safely more than once.

## Acceptance Criteria

- A user with no explicit project still gets private memory behavior.
- Two users can share a project and both can insert/search/ask within it.
- A user cannot read, retrieve, ask, or supersede facts in a project where they
  are not a member.
- `owner_id` continues to identify the actor who wrote or corrected memory.
- `project_id` controls the workspace where memory is visible.
- Vector candidates from other projects are filtered before any response is
  returned or answer text is generated.
- Duplicate conversation hashes dedupe only within the same project.
