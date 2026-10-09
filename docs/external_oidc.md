# External OIDC and Keycloak

Use `api.auth: oidc_resource_server` when an external OpenID Connect provider
issues access tokens for ai-memory-hub. The initial tested profile targets
Keycloak, but validation uses standard discovery and JWKS endpoints rather than
Keycloak administration APIs.

Install the OAuth dependencies before enabling this mode:

```bash
uv sync --extra oauth
```

This mode is separate from `oauth_resource_server`, which accepts hub-issued
HS256 tokens from the built-in Connect authorization flow. The two token models
cannot be enabled together.

## Hub configuration

```yaml
api:
  host: 127.0.0.1
  port: 8000
  auth: oidc_resource_server
  public_base_url: https://memory.example.internal
  oidc:
    issuer: https://keycloak.example.internal/realms/home
    client_id: ai-memory-hub
    audience: ai-memory-hub
    algorithms: [RS256]
    allowed_groups: [memory-users]
    allowed_roles: [memory-user]
    role_scopes:
      memory-reader: [memory:read]
      memory-writer: [memory:read, memory:write]
```

`discovery_url` defaults to `<issuer>/.well-known/openid-configuration`.
Set `jwks_url` only when the provider does not publish a usable discovery
document. HTTPS is required except for loopback development URLs.

The validator requires a signed token with matching `iss`, `aud`, `sub`, and
`exp` claims. It also checks `nbf`, `iat`, and `azp` when present. Direct `scope`
or `scp` values are accepted, and configured Keycloak realm or client roles can
add hub scopes through `role_scopes`. Protected requests still require
`memory:read` or `memory:write` according to the HTTP or MCP operation.

Identity ownership is derived from a SHA-256 digest of the issuer and subject.
This keeps the same user stable across restarts while preventing subject
collisions between providers. Access tokens and raw identity claims are not
persisted or logged by this validation path.

## Keycloak client setup

Create an OpenID Connect client in the selected realm:

1. Set the client ID to the configured `client_id`, such as
   `ai-memory-hub`.
2. Configure Keycloak to include that client in the access-token audience.
3. Add realm or client roles used by `allowed_roles` and `role_scopes`.
4. Add a `groups` mapper if `allowed_groups` is configured.
5. Give users only the roles and groups needed for their hub access.

The hub is a resource server and does not need a client secret to validate
tokens. Redirect URIs, PKCE, refresh tokens, and interactive login remain the
responsibility of the MCP client and Keycloak. The protected-resource metadata
endpoint advertises the configured Keycloak issuer so capable MCP clients can
discover the external authorization server.

## Verification

```bash
curl -fsS https://memory.example.internal/.well-known/oauth-protected-resource/mcp

curl -fsS https://memory.example.internal/memory/search \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"project memory"}'
```

`GET /ready` reports the external issuer, discovery URL, configured audience,
and whether a direct JWKS URL is configured. It never returns tokens or client
secrets.

Use TLS even on a private LAN unless every hop is otherwise protected. Do not
copy browser tokens into committed configuration or shell history. If Keycloak
is only reachable through an internal CA, install that CA in the hub container
or host rather than disabling certificate verification.

## Current boundary

This first external-provider slice validates bearer access tokens and publishes
MCP-compatible protected-resource metadata. It does not add a second browser
login flow to `/connect`, provision Keycloak users into hub metadata, or manage
Keycloak sessions. Those remain provider-owned concerns.
