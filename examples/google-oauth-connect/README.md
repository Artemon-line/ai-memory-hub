# Google OAuth Connect Example

This example starts ai-memory-hub with `api.auth: oauth_resource_server` and the
server-rendered `/connect` setup UI. The passport provider list is configured
from `api.connect.passport`; this example enables Google, while the hub config
also has first-class Meta and X provider slots. The selected provider proves the
user identity; the hub issues short-lived MCP bearer tokens for `memory:read`
and `memory:write`.

Use this for local first-run testing. Before publishing beyond loopback, put the
service behind HTTPS, a VPN, SSH tunnel, or a trusted private network boundary.

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

## Run

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

Restart behavior:

- Identities and web sessions persist while the Compose volume and
  `AMH_SESSION_SECRET` stay the same.
- Hub bearer tokens remain valid until expiry or logout while the metadata
  volume and `AMH_OAUTH_JWT_SECRET` stay the same.
- Changing either secret invalidates the matching session or token class.
