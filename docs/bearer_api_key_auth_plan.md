# Bearer/API-Key Auth Plan

## Goal

Protect ai-memory-hub when MCP/API is exposed beyond localhost, especially for
trusted LAN use. This is not OAuth. It is a pragmatic shared-secret layer that
blocks accidental open access while keeping Codex/opencode setup simple.

## Current Status

Implemented:

- [x] Config placeholders exist:
  - `api.auth: none`
  - `api.api_key: ""`
- [x] Docs warn not to expose the API/MCP endpoint beyond localhost without auth.
- [x] MCP and HTTP API are both mounted by the same FastAPI app.

Not implemented yet:

- [ ] Auth enforcement middleware.
- [ ] Bearer token support.
- [ ] `X-API-Key` support.
- [ ] Different behavior for local-only vs LAN mode.
- [ ] Auth examples for Codex and opencode.
- [ ] Tests for HTTP API and `/mcp/`.

## Threat Model

This plan protects against:

- Other devices on the LAN casually reading or writing memory.
- Accidental exposure through `0.0.0.0:8000`.
- Drive-by calls to `/memory/*` and `/mcp/`.

This plan does not protect against:

- A compromised trusted client machine.
- Token leakage in shell history or config files.
- Internet exposure without TLS.
- Multi-user authorization or per-client scopes.

Use OAuth or a real identity proxy later if the service leaves a trusted LAN or
needs multiple users with different permissions.

## Auth Semantics

Config:

```yaml
api:
  auth: none      # none | api_key
  api_key: ""     # required when auth=api_key
```

Accepted request headers when `api.auth: api_key`:

```http
Authorization: Bearer <api_key>
```

or:

```http
X-API-Key: <api_key>
```

Rules:

- `none`: allow all requests.
- `api_key`: require one valid token for protected routes.
- Use constant-time comparison with `hmac.compare_digest`.
- Missing token returns `401`.
- Wrong token returns `403`.
- Do not log the supplied token.
- Do not echo auth errors into MCP tool payloads; reject before MCP handling.

Protected paths:

- `/memory/insert`
- `/memory/search`
- `/memory/retrieve`
- `/memory/ask`
- `/mcp/*`

Public paths:

- `/health`
- `/ready`
- `/docs`, `/openapi.json`, and `/redoc` should be configurable. For LAN
  default, keep them public. For production, protect or disable them.

## Implementation Plan

### Phase 1: Config Validation

- [ ] Add `APIConfig.auth` validator:
  - allowed: `none`, `api_key`
- [ ] Add model validator:
  - if `auth=api_key`, `api_key` must be non-empty
- [ ] Add tests in `tests/unit/test_config.py`.

### Phase 2: Middleware

Add `memory/api/auth.py`:

- `extract_api_key(request) -> str | None`
- `is_public_path(path, config) -> bool`
- `install_auth_middleware(app, config)`

Implementation sketch:

```python
import hmac
from fastapi import Request
from starlette.responses import JSONResponse


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _valid_token(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, expected)
```

Tasks:

- [ ] Install middleware in `create_app()` before routes are used.
- [ ] Protect mounted MCP app path `/mcp`.
- [ ] Keep auth logic independent from ingestion code.
- [ ] Return JSON for HTTP failures:

```json
{"detail":"missing API key"}
```

or:

```json
{"detail":"invalid API key"}
```

### Phase 3: MCP Compatibility

MCP clients need headers.

Codex config should support headers if its MCP config supports them. If not, use
a local reverse proxy later. The plan should document both paths:

Preferred:

```toml
[mcp_servers.ai_memory_hub]
url = "http://<HOST_LAN_IP>:8000/mcp/"

[mcp_servers.ai_memory_hub.headers]
Authorization = "Bearer ${AI_MEMORY_HUB_API_KEY}"
```

Fallback:

- Run ai-memory-hub bound to `127.0.0.1`.
- Use SSH tunnel from other machine.
- Or use Caddy/Traefik/nginx on the LAN to inject auth only if the client cannot
  set headers directly.

opencode remote MCP config should document headers if supported:

```jsonc
{
  "mcp": {
    "ai_memory_hub": {
      "type": "remote",
      "url": "http://<HOST_LAN_IP>:8000/mcp/",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer $AI_MEMORY_HUB_API_KEY"
      }
    }
  }
}
```

Before implementation, verify the current Codex/opencode header syntax against
official docs or local client behavior. Do not guess this into the README.

### Phase 4: Compose Examples

Add an auth-enabled example override:

```yaml
api:
  host: 0.0.0.0
  port: 8000
  auth: api_key
  api_key: "${AI_MEMORY_HUB_API_KEY}"
```

Important: the current config loader does not expand environment variables in
YAML. Choose one:

- Add environment-variable expansion for selected config values.
- Or mount a real untracked `config.local.yaml`.

Recommended:

- Keep checked-in `config.yaml` unauthenticated for local smoke.
- Add `config.auth.example.yaml`.
- Document:

```bash
cp config.auth.example.yaml config.local.yaml
chmod 600 config.local.yaml
```

Then run:

```bash
AMH_CONFIG_FILE=config.local.yaml docker compose up --build
```

Add `.gitignore` for:

```text
examples/postgres/pgvector/config.local.yaml
```

### Phase 5: Tests

HTTP API tests:

- [ ] `auth=none` allows `/memory/search`.
- [ ] `auth=api_key` rejects missing key with `401`.
- [ ] `auth=api_key` rejects wrong key with `403`.
- [ ] `Authorization: Bearer ...` succeeds.
- [ ] `X-API-Key: ...` succeeds.
- [ ] `/health` remains public.

MCP tests:

- [ ] `/mcp/` initialize rejects missing key.
- [ ] `/mcp/` initialize accepts bearer token.
- [ ] `/mcp/` tools/list accepts bearer token and session id.
- [ ] Auth rejection happens before tool execution.

Security tests:

- [ ] Token never appears in logs.
- [ ] Redaction catches `api_key=<value>`.
- [ ] Timing-safe comparison function is used.

### Phase 6: Documentation

Update:

- README security section.
- `examples/postgres/pgvector/codex_opencode_docker_pgvector_test.md`.
- `docs/agents.md`.

Document three supported modes:

1. Local-only, no auth:

```yaml
ports:
  - "127.0.0.1:8000:8000"
api:
  auth: none
```

2. LAN test with API key:

```yaml
ports:
  - "0.0.0.0:8000:8000"
api:
  auth: api_key
```

3. Internet exposure:

Not supported directly. Put ai-memory-hub behind a TLS reverse proxy or VPN and
revisit OAuth/OIDC.

## Acceptance Criteria

- `api.auth: api_key` protects both `/memory/*` and `/mcp/*`.
- Existing no-auth local tests keep passing.
- Codex/opencode setup can pass a bearer token or has a documented fallback.
- No API key appears in logs, traces, or test failure output.
- LAN docs no longer call unauthenticated `0.0.0.0:8000` the recommended mode.
