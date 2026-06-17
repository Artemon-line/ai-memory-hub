# Bearer Auth And User Isolation Plan

## Goal

Protect ai-memory-hub MCP and HTTP access beyond local testing, starting with a
simple bearer-token mode that supports per-user memory isolation. Keep the design
compatible with MCP HTTP authorization expectations so OAuth resource-server mode
can be added later without changing client request shape.

Supported modes:

- `api.auth: none` for CI and loopback-only local testing.
- `api.auth: bearer_token` for local/LAN deployments that need simple personal
  access tokens and per-user memory isolation.
- `api.auth: oauth_resource_server` later for MCP-compliant OAuth authorization.

Source: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization

## Recommendation

Implement `bearer_token` first. It is not full OAuth, but it uses the same client
request shape required by MCP HTTP auth:

```http
Authorization: Bearer <token>
```

Do not implement `X-API-Key`.

Do not put user keys in `config.yaml`. Store users and token hashes in the
metadata database so an admin CLI/API/UI can create, revoke, expire, and rotate
tokens without editing config.

Use `api.auth: none` only when:

- Running in CI.
- Running loopback-only local development, for example `127.0.0.1:8000`.
- Running isolated smoke tests where the service is not exposed to other hosts.

Use `api.auth: bearer_token` before exposing AMH on a trusted LAN. Use HTTPS,
VPN, SSH tunnel, or a trusted reverse proxy if the endpoint crosses machines.
Use `api.auth: oauth_resource_server` later for internet-facing or federated
Google/Apple/Meta-style login.

## Current Status

Implemented:

- [x] Config placeholders exist:
  - `api.auth: none`
  - `api.auth: bearer_token`
- [x] Docs warn not to expose the API/MCP endpoint beyond localhost without auth.
- [x] MCP and HTTP API are both mounted by the same FastAPI app.
- [x] Auth enforcement middleware protects `/memory/*` and `/mcp/*`.
- [x] SQLite and Postgres metadata providers store users and token hashes.
- [x] Conversations and facts are stamped with server-derived `owner_id`.
- [x] Search, retrieve, ask, fact search, profile, and fact supersession are
      scoped by `owner_id`.
- [x] Vector candidates are filtered through metadata ownership before returning
      or answering.

Not implemented yet:

- [x] Admin CLI commands for user, bearer-token, project, and membership
      management.
- [x] Project workspace membership for shared collaboration. See
      `project_workspace_collaboration_plan.md`.
- [ ] MCP OAuth protected resource metadata.
- [ ] MCP `WWW-Authenticate` challenges with `resource_metadata` and scope hints.
- [ ] OAuth access-token validation with audience/resource binding.
- [ ] Scope checks for read/write/admin operations.
- [ ] Tests for HTTP API and `/mcp/`.

## Threat Model

This plan protects against:

- Other devices reading or writing memory when the service is exposed beyond
  loopback.
- Accidental unauthenticated exposure through `0.0.0.0:8000`.
- Drive-by calls to `/memory/*` and `/mcp/`.
- MCP clients accidentally sending credentials through unsupported paths.
- Access tokens issued for other resources being reused against ai-memory-hub.
- One valid user reading or writing another user's memory.

This plan does not protect against:

- A compromised trusted client machine.
- Authorization server compromise.
- Token leakage in client config, shell history, logs, or traces.
- Internet exposure without TLS.

Internet-facing MCP must use HTTPS and `api.auth: oauth_resource_server`, or run
behind an identity-aware reverse proxy that performs equivalent OAuth
resource-server validation and forwards only trusted requests.

## MCP Authorization Requirements

For HTTP-based MCP transports, the MCP spec says authorization is optional, but
when supported the implementation should conform to the MCP authorization spec.

Server-side requirements that affect ai-memory-hub:

- [ ] Treat the MCP server as an OAuth resource server.
- [ ] Accept access tokens through `Authorization: Bearer <access-token>`.
- [ ] Require authorization on every HTTP request to protected MCP endpoints.
- [ ] Do not accept access tokens in URI query strings.
- [ ] Validate tokens before processing MCP requests.
- [ ] Validate that presented tokens were issued for this MCP server as the
      intended resource/audience.
- [ ] Do not pass inbound MCP access tokens through to downstream services.
- [ ] Return `401 Unauthorized` for missing, invalid, or expired tokens.
- [ ] Return `403 Forbidden` for valid tokens with insufficient scope.
- [ ] Include `WWW-Authenticate: Bearer ...` challenges for auth failures.
- [ ] Expose OAuth Protected Resource Metadata.
- [ ] Include a `resource_metadata` URL in `WWW-Authenticate` challenges.
- [ ] Prefer including a `scope` parameter in challenges so clients can request
      least-privilege access.

Discovery requirements:

- [ ] Serve protected resource metadata at well-known URIs:
  - `/.well-known/oauth-protected-resource`
  - `/.well-known/oauth-protected-resource/mcp` when `/mcp` identifies the MCP
    resource.
- [ ] Metadata includes `authorization_servers` with at least one configured
      authorization server.
- [ ] Metadata includes the MCP server resource identifier.
- [ ] Metadata advertises supported scopes.

Client-facing compatibility requirements:

- [ ] The canonical MCP resource URI is configurable because loopback,
      reverse-proxy, LAN, and HTTPS deployments have different public URLs.
- [ ] The canonical URI must be absolute and must not include a fragment.
- [ ] Use the most specific stable MCP URI when the path matters, for example
      `https://memory.example.com/mcp`.

## Auth Modes

Config:

```yaml
api:
  auth: none                 # none | bearer_token | oauth_resource_server
  public_base_url: ""        # required for oauth_resource_server
  token:
    hash_secret_env: AMH_TOKEN_HASH_SECRET
    default_expiry_days: 365
  oauth:
    authorization_servers: []
    resource: ""             # defaults to public_base_url + /mcp when unset
    scopes_supported:
      - memory:read
      - memory:write
      - memory:admin
```

Rules:

- `none`: allow all requests. Use only for CI and loopback-only local testing.
- `bearer_token`: require a server-issued personal access token in the
  `Authorization: Bearer` header, map it to a user, then scope every read/write
  to that user.
- `oauth_resource_server`: require MCP-compliant Bearer access tokens for
  protected routes.

Protected paths:

- `/memory/insert`
- `/memory/search`
- `/memory/retrieve`
- `/memory/ask`
- `/memory/facts/search`
- `/memory/profile/get`
- `/memory/facts/supersede`
- `/mcp/*`

Public paths:

- `/health`
- `/ready`
- `/.well-known/oauth-protected-resource`
- `/.well-known/oauth-protected-resource/*`
- `/docs`, `/openapi.json`, and `/redoc` should be configurable. For production,
  protect or disable them.

## P0: Simple Bearer Tokens And Per-User Isolation

This is the first implementation target.

Implementation sequence:

- [x] Add config validation:
  - [x] allowed `api.auth` values: `none`, `bearer_token`,
    `oauth_resource_server`
  - [ ] `bearer_token` requires a token hash secret from environment or a
    generated local secret file outside the repo
  - [ ] warn when `auth=none` is used with a non-loopback bind address
- [x] Add metadata-store tables/records for auth:
  - [x] `users`
  - [x] `auth_tokens`
  - [x] token hash, token id, token prefix, display name, created/expires/revoked
        timestamps
  - [ ] scopes and last-used timestamps
  - [x] store only token hashes, never raw tokens
- [x] Add admin CLI commands:
  - [x] `aim admin user create <user_id> [--display-name ...]`
  - [x] `aim admin user list`
  - [x] `aim admin token create --user <user_id> [--display-name ...]`
  - [x] `aim admin token list --user <user_id>`
  - [x] `aim admin token revoke <token_id_or_prefix>`
  - [x] print raw token only once on creation
- [x] Add HTTP auth middleware:
  - [x] parse `Authorization: Bearer <token>`
  - [x] reject missing/invalid/revoked/expired tokens with `401`
  - [ ] reject insufficient scope with `403`
  - [x] keep `/health` and `/ready` public
  - [x] protect `/memory/*` and `/mcp/*`
  - [x] reject or ignore tokens in query strings
- [ ] Add request principal context:
  - [ ] `user_id`
  - [ ] token id
  - [ ] scopes
  - [ ] auth mode
- [x] Add per-user ownership:
  - [x] stamp new conversations with server-side `owner_id`
  - [x] stamp extracted facts with `owner_id`
  - [x] do not trust client-supplied `metadata.owner_id`
  - [x] filter search/retrieve/ask/fact/profile operations by `owner_id`
  - [x] prevent direct retrieval of another user's memory by id
- [ ] Add vector isolation:
  - [ ] include `owner_id` in vector rows where provider supports metadata
  - [x] for providers without vector-side filters, filter candidates after
        metadata lookup before returning or answering
  - [x] add tests proving no cross-user leakage through vector candidates
- [ ] Add scopes:
  - [ ] `memory:read` for search/retrieve/ask/fact search/profile
  - [ ] `memory:write` for validate/insert/fact supersession
  - [ ] `memory:admin` for future admin UI/API
- [ ] Add redaction:
  - [ ] redact `Authorization: Bearer ...` in logs and errors
  - [ ] avoid returning token hashes
  - [ ] never log raw generated tokens
- [x] Add tests:
  - [x] no-auth mode still passes existing tests
  - [x] bearer mode rejects missing/invalid/revoked/expired token
  - [x] bearer mode accepts valid token
  - [x] user A cannot search/retrieve/ask user B memory
  - [x] user A cannot access user B facts/profile
  - [ ] insufficient scope returns `403`
  - [x] admin CLI creates and revokes tokens

Acceptance criteria:

- A Raspberry Pi or LAN deployment can be protected with personal access tokens.
- Each token maps to exactly one user/principal.
- Every memory/fact read and write is scoped to the token's user.
- Raw tokens are shown once and only token hashes are stored.
- Existing CI/local `auth=none` behavior remains available.
- The MCP client request shape is already compatible with future OAuth mode.

## OAuth Resource-Server Mode

Use `api.auth: oauth_resource_server` whenever the HTTP MCP endpoint is reachable
outside CI or loopback-only local testing.

Token validation should be adapter-based so the first implementation can support
one practical provider without hardcoding the project to a specific IdP:

- JWT validation through JWKS.
- Token introspection through a configured authorization server.
- Identity-aware reverse proxy headers only when the proxy is trusted and strips
  spoofed inbound headers.

Required validation:

- [ ] Signature or introspection validity.
- [ ] Expiration.
- [ ] Issuer, when configured.
- [ ] Audience/resource matches the configured MCP resource URI.
- [ ] Required scopes for the requested route or MCP operation.

Do not:

- [ ] Accept arbitrary third-party access tokens.
- [ ] Accept tokens missing this MCP server in their audience/resource claim.
- [ ] Forward inbound MCP access tokens to OpenAI, Ollama, databases, vector
      stores, or other upstream APIs.
- [ ] Put tokens in query strings, logs, traces, or MCP tool payloads.

Recommended initial scopes:

- `memory:read`: search, retrieve, ask, resource reads.
- `memory:write`: validate, insert, fact supersession.
- `memory:admin`: config, debug, or observability summaries if exposed.

Scope failures:

- Missing/invalid/expired token: `401` with `WWW-Authenticate`.
- Valid token but insufficient scope: `403` with
  `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`.

## Implementation Plan

### Phase 1: Config Validation

- [ ] Add `APIConfig.auth` validator:
  - allowed: `none`, `oauth_resource_server`
- [ ] Add model validators:
  - if `auth=oauth_resource_server`, `public_base_url` and at least one
    authorization server must be configured
  - if `oauth.resource` is set, it must be an absolute URI with no fragment
  - `auth=none` is allowed but should warn when binding to non-loopback hosts
- [ ] Remove or deprecate `api.api_key` from the plan and docs.
- [ ] Add tests in `tests/unit/test_config.py`.

### Phase 2: Auth Middleware

Add `memory/api/auth.py`:

- `is_public_path(path, config) -> bool`
- `install_auth_middleware(app, config)`
- `extract_bearer_token(request) -> str | None`
- `build_www_authenticate_challenge(request, config, scopes=None, error=None) -> str`
- `required_scopes_for_request(request) -> set[str]`

Tasks:

- [ ] Install middleware in `create_app()` before routes are used.
- [ ] Protect mounted MCP app path `/mcp`.
- [ ] Keep auth logic independent from ingestion code.
- [ ] Return HTTP auth failures before MCP handling.
- [ ] Return `WWW-Authenticate` on protected-path `401` responses.
- [ ] Keep `/health`, `/ready`, and protected-resource metadata public.
- [ ] Ignore or reject query-string tokens.
- [ ] Do not support `X-API-Key`.

### Phase 3: Protected Resource Metadata

Add endpoints:

- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-protected-resource/mcp`

Metadata fields:

- [ ] Resource identifier for the MCP server.
- [ ] `authorization_servers`.
- [ ] `scopes_supported`.
- [ ] Optional service documentation URLs if useful.

Tasks:

- [ ] Generate metadata from config.
- [ ] Add tests for root metadata.
- [ ] Add tests for `/mcp` path metadata.
- [ ] Ensure metadata responses do not expose secrets.

### Phase 4: OAuth Token Validation

Add a token-validator abstraction:

- `AccessTokenClaims`
- `TokenValidationResult`
- `TokenValidator`
- `JWKSJWTTokenValidator`
- Optional later: `IntrospectionTokenValidator`

Tasks:

- [ ] Validate signature or introspection result.
- [ ] Validate expiration.
- [ ] Validate issuer when configured.
- [ ] Validate audience/resource against configured MCP resource URI.
- [ ] Validate scopes for protected route or MCP operation.
- [ ] Reject token passthrough; inbound tokens are only for authorizing
      ai-memory-hub.

### Phase 5: Scope Mapping

Initial route and MCP operation mapping:

- [ ] `memory:read`:
  - `/memory/search`
  - `/memory/retrieve`
  - `/memory/ask`
  - `/memory/facts/search`
  - `/memory/profile/get`
  - MCP resource reads
  - MCP search, retrieve, ask, fact search, and profile tools
- [ ] `memory:write`:
  - `/memory/insert`
  - `/memory/facts/supersede`
  - MCP validate, insert, and fact supersession tools
- [ ] `memory:admin`:
  - future protected observability, debug, config, or maintenance endpoints

When scope is insufficient:

- [ ] Return `403`.
- [ ] Include `WWW-Authenticate: Bearer error="insufficient_scope"`.
- [ ] Include the minimum required `scope` value.
- [ ] Include `resource_metadata`.

### Phase 6: MCP Client Compatibility

MCP clients must send `Authorization: Bearer <access-token>` on every HTTP
request to protected MCP endpoints. Clients should discover authorization
servers from protected resource metadata and request tokens for the configured
MCP resource URI.

Before documenting exact client syntax, verify current Codex and opencode MCP
authorization behavior against official docs or local client behavior.

Fallbacks for clients without MCP authorization support:

- Run ai-memory-hub bound to `127.0.0.1`.
- Use SSH tunnel from another machine.
- Use an identity-aware reverse proxy that handles OAuth and only forwards
  authenticated requests.

### Phase 7: Compose Examples

Keep checked-in Compose examples unauthenticated only for local smoke tests bound
to loopback.

Add a separate OAuth/proxy example instead of a shared-secret LAN example:

- [ ] Example reverse proxy with TLS.
- [ ] Example `api.auth: oauth_resource_server` config.
- [ ] Example protected resource metadata config.
- [ ] Example JWKS or introspection validator config.

Do not document unauthenticated `0.0.0.0:8000` as a recommended mode.

### Phase 8: Tests

HTTP API tests:

- [ ] `auth=none` allows `/memory/search` in test config.
- [ ] `auth=none` with non-loopback bind emits a warning.
- [ ] `/health` remains public.
- [ ] `/ready` remains public.
- [ ] Protected resource metadata remains public and secret-free.

MCP tests:

- [ ] `/mcp/` initialize rejects missing Bearer token when OAuth auth is enabled.
- [ ] `/mcp/` initialize accepts valid Bearer auth.
- [ ] `/mcp/` tools/list accepts valid Bearer auth and session id.
- [ ] Auth rejection happens before tool execution.
- [ ] Query-string tokens are rejected or ignored.
- [ ] `WWW-Authenticate` includes `resource_metadata`.

OAuth resource-server tests:

- [ ] Valid token with correct audience/resource succeeds.
- [ ] Valid token with wrong audience/resource fails with `401`.
- [ ] Expired token fails with `401`.
- [ ] Valid token without required scope fails with `403`.
- [ ] Insufficient-scope response includes `error="insufficient_scope"` and
      required `scope`.
- [ ] Inbound access token is not forwarded to provider calls.

Security tests:

- [ ] Token never appears in logs.
- [ ] Redaction catches `Authorization: Bearer <value>`.
- [ ] Query-string token attempts do not leak through access logs.

### Phase 9: Documentation

Update:

- README security section.
- `examples/postgres/pgvector/codex_opencode_docker_pgvector_test.md`.
- `docs/agents.md`.
- `docs/mcp_plan.md`.

Document three supported modes:

1. CI and loopback-only local testing:

```yaml
ports:
  - "127.0.0.1:8000:8000"
api:
  auth: none
```

2. MCP-compliant HTTP auth:

```yaml
api:
  auth: oauth_resource_server
  public_base_url: "https://memory.example.com"
  oauth:
    authorization_servers:
      - "https://auth.example.com"
    resource: "https://memory.example.com/mcp"
```

3. Internet exposure without built-in OAuth:

Put ai-memory-hub behind TLS plus an identity-aware reverse proxy, or expose it
only through a VPN. Do not publish unauthenticated MCP/API endpoints.

## Acceptance Criteria

- `api.auth` supports `none`, `bearer_token`, and `oauth_resource_server`.
- `api.auth: none` remains available for CI and loopback-only local testing.
- `api.auth: bearer_token` protects `/memory/*` and `/mcp/*` with
  `Authorization: Bearer <token>`.
- `api.auth: oauth_resource_server` exposes MCP protected resource metadata.
- Protected MCP responses include proper Bearer challenges.
- OAuth mode validates token audience/resource before processing MCP requests.
- OAuth mode uses `401` for missing/invalid tokens and `403` for insufficient
  scopes.
- Existing no-auth local and CI tests keep passing.
- Codex/opencode OAuth behavior is verified before exact setup syntax is added
  to user-facing docs.
- No access token appears in logs, traces, MCP payloads, or test failure output.
- Docs do not recommend unauthenticated `0.0.0.0:8000`.
