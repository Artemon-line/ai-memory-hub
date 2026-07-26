# Connect UI and OAuth Setup

<section class="amh-connect-hero">
  <p class="amh-eyebrow">Local MCP setup</p>
  <h2>Connect clients to one memory endpoint</h2>
  <p>The Connect UI is the user-facing setup page for MCP clients. It shows the active MCP URL and renders copyable URL-only client setup snippets with a style that matches the app page.</p>
  <a href="http://127.0.0.1:8000/connect">Open local Connect UI</a>
</section>

Open it at:

```text {.amh-copy-block}
http://127.0.0.1:8000/connect
```

## Responsibility Boundary

<div class="amh-connect-grid">
<section class="amh-connect-card">
<h3>Connect page</h3>

The MCP client owns sign-in, reauthentication, and token storage:

- `/connect` renders setup and client snippets.
- `/connect` does not ask humans to sign in or copy bearer tokens.
- Clients should use the MCP OAuth metadata and authorization flow when auth is
  needed.

</section>
<section class="amh-connect-card">
<h3>Protected resource</h3>

The MCP endpoint remains a protected resource server:

- `/mcp` and `/memory/*` require a valid bearer token when
  `api.auth: oauth_resource_server` is enabled.
- The hub exposes protected-resource metadata and OAuth authorization-server
  metadata for compliant MCP clients.
- Client account switching is client-driven: use the client's reauth/logout
  flow rather than copying tokens from `/connect`.

</section>
</div>

## Access Modes

`/connect` renders whenever `api.connect.enabled: true`. The page adapts to the
configured auth mode:

<div class="amh-client-matrix">
<article>
<h3>No auth <span class="pending">Local</span></h3>
<code>api.auth: none</code>
<p>Shows the endpoint and client snippets for loopback or trusted-network development. Do not expose this mode to untrusted networks.</p>
</article>
<article>
<h3>Bearer/API key <span>Protected</span></h3>
<code>api.auth: bearer_token</code>
<p>Shows setup guidance without rendering configured secrets. Client header support still needs client-specific verification.</p>
</article>
<article>
<h3>OAuth resource server <span>Preferred</span></h3>
<code>api.auth: oauth_resource_server</code>
<p>Shows OAuth-oriented setup. MCP clients own sign-in, reauthentication, and token storage.</p>
</article>
</div>

## Safe Diagnostics

The Connect UI includes a small diagnostics panel for setup and support. It is
intentionally allowlisted:

- Service readiness mode.
- Metadata, vector, and embedding provider names.
- Vector fallback state.
- Structured logging state.
- OpenTelemetry tracing and metrics state.
- Whether an OTLP endpoint is in use.

The panel does not render memory counts, user identities, bearer tokens, API
keys, DSNs, raw queries, embeddings, or request payloads.

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

```bash {.amh-copy-block}
uv sync --extra oauth
```

For package installs:

```bash {.amh-copy-block}
pip install "ai-memory-hub[oauth]"
```

The `oauth` extra installs Authlib and HTTPX. Without it, the Connect UI can
render, but live OAuth sign-in returns a configuration error.

## Google Setup

Create an OAuth client in Google Cloud and add the exact redirect URI used by
your hub config. For the local example:

```text {.amh-copy-block}
http://127.0.0.1:8000/auth/google/callback
```

Export these secrets before starting the hub:

```bash {.amh-copy-block}
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
```

`AMH_OAUTH_JWT_SECRET` signs hub-issued MCP bearer tokens returned by the
OAuth token endpoint. Keep it stable while you want existing tokens to remain
valid.

`AMH_SESSION_SECRET` signs browser session state. Keep it stable while you want
browser sign-in sessions to survive restarts.

## Hub Config

User-facing MCP setup should use `api.auth: oauth_resource_server`, set
`api.public_base_url`, and enable `api.connect`.

```yaml {.amh-copy-block}
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

```bash {.amh-copy-block}
uv sync --extra oauth
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
uv run aim serve --config examples/google-oauth-connect/config.yaml --host 127.0.0.1 --port 8000
```

Then open:

```text {.amh-copy-block}
http://127.0.0.1:8000/connect
```

## Docker Compose

The Docker example installs `--extra oauth` in the image and binds the service
to loopback:

```bash {.amh-copy-block}
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
cd examples/google-oauth-connect
docker compose up --build
```

Open:

```text {.amh-copy-block}
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

<div class="amh-client-matrix">
<article>
<h3>Codex <span>Verified</span></h3>
<code>codex mcp add ai-memory-hub-local --url &lt;mcp-url&gt;</code>
<p>Verified for streamable HTTP setup. Use the MCP URL shown by `/connect`.</p>
</article>
<article>
<h3>Copilot CLI <span class="pending">Unverified</span></h3>
<code>copilot mcp add --transport http ai-memory-hub http://127.0.0.1:8000/mcp</code>
<p>Confirm exact command and persistence location.</p>
</article>
<article>
<h3>Claude CLI <span>Verified</span></h3>
<code>claude mcp add --transport http ai-memory-hub-local &lt;mcp-url&gt;</code>
<p>Verified for streamable HTTP setup. Use the MCP URL shown by `/connect`.</p>
</article>
<article>
<h3>Gemini CLI <span>Verified</span></h3>
<code>gemini mcp add ai-memory-hub-local &lt;mcp-url&gt; -t http</code>
<p>After adding it, run `/mcp auth` inside Gemini CLI.</p>
</article>
<article>
<h3>OpenCode <span>Verified</span></h3>
<code>opencode mcp add ai-memory-hub-local --url &lt;mcp-url&gt;</code>
<p>After adding it, run `opencode mcp add ai-memory-hub-local auth`.</p>
</article>
<article>
<h3>Pi <span>Verified</span></h3>
<code>pi install npm:pi-mcp-adapter</code>
<p>Install the adapter, export an existing MCP config from Codex or OpenCode, then run `/mcp auth` inside Pi.</p>
</article>
<article>
<h3>Hermes <span>Verified</span></h3>
<code>hermes mcp add ai-memory-hub-local --url &lt;mcp-url&gt; --auth oauth</code>
<p>Verified for OAuth-backed streamable HTTP setup.</p>
</article>
<article>
<h3>OpenShell <span class="pending">Unverified</span></h3>
<code>MCP URL</code>
<p>Confirm OAuth behavior.</p>
</article>
<article>
<h3>OpenClaw <span class="pending">Unverified</span></h3>
<code>MCP URL</code>
<p>Confirm OAuth behavior.</p>
</article>
</div>

Do not commit OAuth client secrets or captured bearer tokens.
