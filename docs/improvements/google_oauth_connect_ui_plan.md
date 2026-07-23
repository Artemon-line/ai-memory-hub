# Google OAuth Connect UI Plan

## Goal

Provide a simple first-run UI that helps users connect ai-memory-hub to MCP
clients with Google sign-in. Google proves user identity; ai-memory-hub issues
the MCP bearer token that controls `memory:read` and `memory:write` access.

User-facing setup should use `api.auth: oauth_resource_server`. Keep
`api.auth: none` reserved for CI/test fixtures and maintainer-only smoke tests.

## Product Shape

Add a small server-rendered Connect UI, not a marketing site:

- `GET /` redirects to or renders the Connect UI.
- `GET /connect` shows service status, MCP URL, auth status, and client setup
  snippets.
- `GET /auth/google` starts Google OAuth/OIDC login.
- `GET /auth/google/callback` validates Google login, creates or finds the local
  user, creates a web session, and issues or displays the hub token workflow.
- `POST /auth/logout` clears the web session.

The UI should show:

- MCP URL, for example `https://memory.example.com/mcp`.
- Signed-in identity, for example `alice@example.com`.
- Copy buttons for client setup snippets.
- Auth support status per client: verified OAuth, bearer fallback, or unverified.
- Account-switching guidance: sign out or clear the client MCP auth token, then
  authenticate again.

## Storage Model

Use the same configured metadata database for durable auth state. SQLite remains
the default local store; Postgres and other metadata providers should implement
the same contract when they support auth.

Store:

- `oauth_identities`
  - provider, initially `google`
  - provider subject, from Google `sub`
  - local `user_id`
  - normalized email and display name
  - created and last-login timestamps
- `web_sessions`
  - hashed session id
  - `user_id`
  - CSRF token hash
  - expiry, created, last-seen, revoked timestamps
- `auth_tokens` or OAuth access-token records
  - token id and hash or JWT id
  - `user_id`
  - scopes
  - expiry and revoked timestamps

Do not store Google access tokens by default. Store Google refresh tokens only if
a later feature truly needs Google APIs; MCP authorization should use hub-issued
tokens.

## Phase 1: Connect UI Skeleton

- [ ] Add config for enabling the Connect UI, default enabled for user-facing
      Docker/runtime setups.
- [ ] Add public Connect UI routes that do not require MCP bearer auth:
      `/`, `/connect`, `/auth/google`, `/auth/google/callback`, and
      `/auth/logout`.
- [ ] Render a minimal HTML page with service status, MCP URL, and auth status.
- [ ] Derive the MCP URL from `api.public_base_url` plus `/mcp` unless
      `api.oauth.resource` is explicitly configured.
- [ ] Add copyable setup snippets with placeholders for Codex, Copilot CLI, Pi,
      OpenCode, Claude, Hermes, OpenShell, OpenClaw, and Gemini CLI.
- [ ] Mark every unverified client snippet as unverified until tested against
      current official docs or local client behavior.
- [ ] Add tests for route availability, secret-free rendering, and correct MCP
      URL derivation.

## Phase 2: Google OAuth/OIDC Login

- [ ] Add config for Google OAuth:
      client id env var, client secret env var, callback URL, allowed hosted
      domains, and allowed email list if configured.
- [ ] Use Authlib or a similarly maintained Python OAuth/OIDC library rather
      than hand-rolling protocol handling.
- [ ] Start Google login from `/auth/google` using OIDC scopes:
      `openid email profile`.
- [ ] Validate the Google callback, ID token, issuer, audience, nonce, state, and
      expiry.
- [ ] Reject users outside configured hosted-domain or email allowlists.
- [ ] Never log Google tokens, ID-token claims beyond safe identifiers, or raw
      callback query strings.
- [ ] Add unit/integration tests for callback success, invalid state, wrong
      audience, expired token, denied domain, and secret redaction.

## Phase 3: Durable Identity And Web Sessions

- [ ] Add `oauth_identities` metadata-store contract methods.
- [ ] Implement Google subject to local user lookup and creation.
- [ ] Add `web_sessions` metadata-store contract methods.
- [ ] Store only hashed session ids and hashed CSRF tokens.
- [ ] Set web-session cookies as `HttpOnly`, `Secure` when not on loopback, and
      `SameSite=Lax`.
- [ ] Make sessions survive server restart when the metadata DB and session
      signing secret are unchanged.
- [ ] Add logout and session revocation.
- [ ] Add migration tests for SQLite and Postgres metadata stores.
- [ ] Add restart tests proving the same Google subject maps to the same
      `owner_id`.

## Phase 4: Hub-Issued MCP Tokens

- [ ] Add a token issuer that creates hub-owned access tokens after Google login.
- [ ] Include `sub`, `iss`, `aud` or `resource`, `scope`, `iat`, `exp`, and `jti`
      claims.
- [ ] Use `api.oauth.resource` or `api.public_base_url + /mcp` as the MCP
      audience/resource.
- [ ] Scope default tokens to `memory:read memory:write`.
- [ ] Keep token expiry short by default.
- [ ] Store revocation state or token hashes where needed to support logout and
      emergency revocation.
- [ ] Reuse existing OAuth resource-server validation for MCP/API requests.
- [ ] Add tests for valid token use, expired token rejection, wrong resource
      rejection, insufficient scope, revocation, and account isolation.

## Phase 5: MCP Client Setup Matrix

- [ ] Create a client setup matrix for:
      Codex, Copilot CLI, Pi, OpenCode, Claude, Hermes, OpenShell, OpenClaw, and
      Gemini CLI.
- [ ] For each client, document:
      config file path or command, exact MCP URL snippet, OAuth support status,
      token storage behavior, reauth/account-switch behavior, and known limits.
- [ ] Verify exact syntax against official docs or local client behavior before
      marking a client as verified.
- [ ] Keep unverified clients visible but labeled `Unverified`.
- [ ] Include copy buttons in the Connect UI and docs.
- [ ] Add tests that generated snippets include the configured MCP URL and never
      include raw tokens.

## Phase 6: Account Switching And Reauth

- [ ] Document that account switching is client-driven.
- [ ] Add UI guidance for clearing the old MCP auth token before signing in with
      another Google account.
- [ ] Make the server treat each Google `sub` as a distinct identity unless an
      admin explicitly links accounts.
- [ ] Add a safe account-linking plan or explicitly defer account linking.
- [ ] Add tests proving User A cannot access User B memory after reauth with a
      different Google account.

## Phase 7: Docker And Release Docs

- [ ] Add a user-facing OAuth-enabled Docker Compose example.
- [ ] Keep test-only unauthenticated Compose examples labeled as maintainer smoke
      tests.
- [ ] Document required environment variables:
      Google client id, Google client secret, hub JWT/session secret,
      `api.public_base_url`, and allowed domains/emails.
- [ ] Document restart behavior:
      identities and sessions persist with the same metadata DB and secrets;
      changing the JWT/session secret invalidates existing tokens/sessions.
- [ ] Update README quickstart to send users to `/connect`.
- [ ] Update MCP docs to make `/connect` the source of exact client setup
      snippets.

## Phase 8: Security, Privacy, And Operations

- [ ] Add CSRF protection for state-changing UI routes.
- [ ] Rate-limit login and callback attempts when a rate-limit mechanism exists.
- [ ] Add audit events for login success, login denial, logout, token issuance,
      token revocation, and account switch.
- [ ] Redact session ids, authorization headers, Google tokens, and hub tokens
      from logs, traces, metrics labels, and error responses.
- [ ] Add readiness/observability status for Connect UI and Google OAuth config
      without exposing secrets.
- [ ] Add negative tests for token leakage in logs and rendered pages.

## Done When

- [ ] A new user can start the OAuth-enabled Docker setup, open `/connect`, sign
      in with Google, copy an MCP URL/snippet, and connect at least one verified
      MCP client.
- [ ] The MCP client sends `Authorization: Bearer <hub-token>` and memory reads
      and writes are scoped to the signed-in user.
- [ ] Server restart preserves identities and active sessions when the metadata
      DB and secrets persist.
- [ ] Reauth with another Google account maps to another local user and cannot
      read the previous user's memory.
- [ ] User-facing docs do not present `api.auth: none` as a setup option.
