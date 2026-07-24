# Connect UI and OAuth Setup

The Connect UI is the user-facing setup page for MCP clients. It handles
browser sign-in, shows the active MCP URL, issues short-lived hub bearer tokens,
and renders copyable client setup snippets.

Open it at:

```text
http://127.0.0.1:8000/connect
```

## Responsibility Boundary

The hub owns human sign-in and hub token issuance:

- `/connect` renders setup and client snippets.
- `/auth/*` starts and completes provider sign-in.
- The hub stores OAuth identities and web sessions.
- The hub issues short-lived MCP bearer tokens.

The MCP endpoint remains a protected resource server:

- `/mcp` and `/memory/*` require a valid bearer token when
  `api.auth: oauth_resource_server` is enabled.
- MCP tools do not perform Google login directly.
- Client account switching is client-driven: clear the old MCP token in the
  client, sign in again at `/connect`, then install the new hub token.

## Supported Providers

Current live support is Google OpenID Connect.

The config model also includes `meta` and `x` provider slots so the Connect UI
can stay provider-shaped, but those providers are not marked supported until
their provider-specific authorization, callback, claim mapping, and tests are
implemented. Treat them as disabled placeholders.

| Provider | Status | Notes |
| --- | --- | --- |
| Google | Supported | Uses Authlib through the `oauth` extra. |
| Meta | Placeholder | Config slot exists; keep disabled. |
| X | Placeholder | Config slot exists; keep disabled. |

## Packages

Base installs include the server-rendered UI dependencies:

- `jinja2`
- `itsdangerous`

Live provider sign-in requires the OAuth extra:

```bash
uv sync --extra oauth
```

For package installs:

```bash
pip install "ai-memory-hub[oauth]"
```

The `oauth` extra installs Authlib and HTTPX. Without it, the Connect UI can
render, but live OAuth sign-in returns a configuration error.

## Google Setup

Create an OAuth client in Google Cloud and add the exact redirect URI used by
your hub config. For the local example:

```text
http://127.0.0.1:8000/auth/google/callback
```

Export these secrets before starting the hub:

```bash
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
```

`AMH_OAUTH_JWT_SECRET` signs hub-issued MCP bearer tokens. Keep it stable while
you want existing tokens to remain valid.

`AMH_SESSION_SECRET` signs browser session state. Keep it stable while you want
browser sign-in sessions to survive restarts.

## Hub Config

User-facing MCP setup should use `api.auth: oauth_resource_server`, set
`api.public_base_url`, and enable `api.connect`.

```yaml
api:
  host: 127.0.0.1
  port: 8000
  auth: oauth_resource_server
  public_base_url: http://127.0.0.1:8000
  oauth:
    authorization_servers:
      - http://127.0.0.1:8000
    resource: http://127.0.0.1:8000/mcp
    jwt_secret_env: AMH_OAUTH_JWT_SECRET
    scopes_supported:
      - memory:read
      - memory:write
      - memory:admin
  connect:
    enabled: true
    session_secret_env: AMH_SESSION_SECRET
    session_ttl_seconds: 43200
    token_ttl_seconds: 3600
    passport:
      providers: [google]
      google:
        enabled: true
        client_id_env: GOOGLE_CLIENT_ID
        client_secret_env: GOOGLE_CLIENT_SECRET
        callback_url: http://127.0.0.1:8000/auth/google/callback
        allowed_domains: []
        allowed_emails: []
      meta:
        enabled: false
        client_id_env: META_CLIENT_ID
        client_secret_env: META_CLIENT_SECRET
      x:
        enabled: false
        client_id_env: X_CLIENT_ID
        client_secret_env: X_CLIENT_SECRET
```

`allowed_domains` and `allowed_emails` are optional allow-lists for Google
identities. Leave both empty for local testing.

## Local Run

From the repository root:

```bash
uv sync --extra oauth
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
uv run aim serve --config examples/google-oauth-connect/config.yaml --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/connect
```

## Docker Compose

The Docker example installs `--extra oauth` in the image and binds the service
to loopback:

```bash
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
cd examples/google-oauth-connect
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000/connect
```

Restart behavior:

- Identities and web sessions persist while the Compose volume and
  `AMH_SESSION_SECRET` stay the same.
- Hub bearer tokens remain valid until expiry or logout while the metadata
  volume and `AMH_OAUTH_JWT_SECRET` stay the same.
- Changing either secret invalidates the matching session or token class.

## Client Verification Matrix

The Connect UI can render snippets before every client has been verified
against the current local release. Keep snippets labeled `Unverified` until the
exact command or config has been tested.

| Client | Status | Setup shape to verify | Notes |
| --- | --- | --- | --- |
| Codex | Unverified | TOML MCP server with `Authorization` header | Verify token refresh and account switching behavior. |
| Copilot CLI | Unverified | `copilot mcp add --transport http --header "Authorization: Bearer <hub-token>" ai-memory-hub http://127.0.0.1:8000/mcp` | Confirm exact command and persistence location. |
| Claude Desktop | Unverified | MCP HTTP server config with bearer header | Confirm current JSON shape. |
| Gemini CLI | Unverified | MCP HTTP server config with bearer header | Confirm current config path and header syntax. |
| OpenCode | Unverified | MCP HTTP server config with bearer header | Confirm current config path and header syntax. |
| Pi | Unverified | MCP HTTP server config with bearer header | Confirm whether custom headers are supported. |
| Hermes | Unverified | MCP URL plus bearer token | Confirm whether custom headers are supported. |
| OpenShell | Unverified | MCP URL plus bearer token | Confirm whether custom headers are supported. |
| OpenClaw | Unverified | MCP URL plus bearer token | Confirm whether custom headers are supported. |

Do not commit real hub tokens or OAuth client secrets. When capturing client
verification notes, redact bearer tokens as `<hub-token>`.
