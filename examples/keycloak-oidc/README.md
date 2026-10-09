# Keycloak OIDC Resource Server Example

This example configures ai-memory-hub to validate Keycloak access tokens through
the realm discovery document and JWKS. Replace the example hostnames, create the
`ai-memory-hub` client and roles in Keycloak, then run:

```bash
uv run aim serve --config examples/keycloak-oidc/config.yaml
```

See [External OIDC and Keycloak](../../docs/external_oidc.md) for the token,
audience, role, group, TLS, and MCP discovery contract.
