# Google OAuth Connect Example

This example starts ai-memory-hub with `api.auth: oauth_resource_server` and the
server-rendered `/connect` setup UI. The selected provider proves the user
identity; the hub issues short-lived MCP bearer tokens for `memory:read` and
`memory:write`.

Google is the current live provider. The hub config also includes disabled
`meta` and `x` provider slots, but those are placeholders until their
provider-specific flows are implemented and tested.

Use this for local first-run testing. Before publishing beyond loopback, put the
service behind HTTPS, a VPN, SSH tunnel, or a trusted private network boundary.
For the full guide, see
[`docs/connect_ui.md`](../../docs/connect_ui.md).

## Packages

The Containerfile installs the `oauth` extra:

```bash
uv sync --frozen --no-dev --extra oauth
```

For local runs outside Docker, install the same extra:

```bash
uv sync --extra oauth
```

## Configure Google

Create an OAuth client in Google Cloud and add this redirect URI:

```text
http://127.0.0.1:8000/auth/google/callback
```

Then export the required secrets:

```bash
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
```

`AMH_OAUTH_JWT_SECRET` signs hub-issued MCP bearer tokens.
`AMH_SESSION_SECRET` signs browser session state. Keep both stable if you want
sessions and tokens to survive restarts.

## Run With Docker Compose

```bash
cd examples/google-oauth-connect
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000/connect
```

After sign-in, copy the one-time hub token and one of the client setup snippets.
All client snippets remain labeled `Unverified` until their exact syntax is
tested against current client releases.

## Run Locally

From the repository root:

```bash
uv sync --extra oauth
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
uv run aim serve --config examples/google-oauth-connect/config.yaml --host 127.0.0.1 --port 8000
```

Restart behavior:

- Identities and web sessions persist while the Compose volume and
  `AMH_SESSION_SECRET` stay the same.
- Hub bearer tokens remain valid until expiry or logout while the metadata
  volume and `AMH_OAUTH_JWT_SECRET` stay the same.
- Changing either secret invalidates the matching session or token class.
