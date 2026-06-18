# OAuth Resource Server Example

This example shows the built-in MCP OAuth resource-server mode behind a TLS
reverse proxy. It is intentionally provider-neutral: ai-memory-hub validates
Bearer tokens for the configured MCP resource and publishes protected resource
metadata, while the authorization server remains external.

## Files

- `config.yaml`: enables `api.auth: oauth_resource_server`.
- `Caddyfile`: terminates HTTPS and proxies to a loopback ai-memory-hub server.

## Run

```bash
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
uv run aim serve --config examples/oauth-resource-server/config.yaml --host 127.0.0.1 --port 8000
caddy run --config examples/oauth-resource-server/Caddyfile
```

The metadata endpoints remain public:

```bash
curl https://memory.example.com/.well-known/oauth-protected-resource
curl https://memory.example.com/.well-known/oauth-protected-resource/mcp
```

Protected requests must send a Bearer token whose audience or resource claim is
`https://memory.example.com/mcp`:

```bash
curl \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  https://memory.example.com/memory/search \
  -d '{"query":"project memory"}'
```

## Validator Status

Current built-in validation supports HS256 JWTs through `api.oauth.jwt_secret`
or `api.oauth.jwt_secret_env`. JWKS and introspection are the planned production
adapters for third-party authorization servers; until those are implemented, put
ai-memory-hub behind a trusted identity-aware proxy or use a local issuer that
can mint HS256 tokens for this resource.
