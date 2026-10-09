from __future__ import annotations

import time

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from memory.api import external_oidc
from memory.api.external_oidc import ExternalOIDCValidator
from memory.config import parse_config


def _config(**overrides: object):
    oidc: dict[str, object] = {
        "issuer": "https://keycloak.example.test/realms/home",
        "client_id": "ai-memory-hub",
        "allowed_domains": ["example.test"],
        "allowed_groups": ["memory-users"],
        "allowed_roles": ["memory-writer"],
        "role_scopes": {"memory-writer": ["memory:write"]},
        **overrides,
    }
    return parse_config(
        {
            "api": {
                "auth": "oidc_resource_server",
                "public_base_url": "https://memory.example.test",
                "oidc": oidc,
            }
        }
    )


def _token(key: RSAKey, **overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://keycloak.example.test/realms/home",
        "aud": "ai-memory-hub",
        "azp": "ai-memory-hub",
        "sub": "keycloak-user-123",
        "email": "alice@example.test",
        "email_verified": True,
        "groups": ["memory-users"],
        "scope": "memory:read",
        "realm_access": {"roles": ["memory-writer"]},
        "iat": now,
        "exp": now + 300,
        "jti": "token-123",
    }
    claims.update(overrides)
    return jwt.encode({"alg": "RS256", "kid": "key-a"}, claims, key)


@pytest.mark.asyncio
async def test_external_oidc_validates_keycloak_claims_and_maps_role_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = RSAKey.generate_key(2048, parameters={"kid": "key-a", "alg": "RS256"})
    requests: list[str] = []

    async def fetch(url: str) -> dict[str, object]:
        requests.append(url)
        if url.endswith("openid-configuration"):
            return {
                "issuer": "https://keycloak.example.test/realms/home",
                "jwks_uri": "https://keycloak.example.test/realms/home/protocol/openid-connect/certs",
            }
        return {"keys": [key.as_dict(private=False)]}

    monkeypatch.setattr(external_oidc, "_fetch_json", fetch)
    validator = ExternalOIDCValidator(_config().api.oidc)

    first = await validator.validate(_token(key))
    second = await validator.validate(_token(key, jti="token-456"))

    assert first is not None
    assert first.owner_id.startswith("oidc:")
    assert "keycloak-user-123" not in first.owner_id
    assert first.token_id == "token-123"
    assert first.scopes == frozenset({"memory:read", "memory:write"})
    assert second is not None
    assert second.owner_id == first.owner_id
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example.test"},
        {"aud": "another-service"},
        {"azp": "another-client"},
        {"exp": 1},
        {"sub": ""},
        {"email": "alice@other.example"},
        {"groups": ["other-group"]},
        {"realm_access": {"roles": ["other-role"]}},
    ],
)
async def test_external_oidc_rejects_invalid_or_disallowed_claims(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    key = RSAKey.generate_key(2048, parameters={"kid": "key-a", "alg": "RS256"})

    async def fetch(url: str) -> dict[str, object]:
        if url.endswith("openid-configuration"):
            return {
                "issuer": "https://keycloak.example.test/realms/home",
                "jwks_uri": "https://keycloak.example.test/keys",
            }
        return {"keys": [key.as_dict(private=False)]}

    monkeypatch.setattr(external_oidc, "_fetch_json", fetch)
    validator = ExternalOIDCValidator(_config().api.oidc)

    assert await validator.validate(_token(key, **overrides)) is None


@pytest.mark.asyncio
async def test_external_oidc_rejects_discovery_with_wrong_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = RSAKey.generate_key(2048, parameters={"kid": "key-a", "alg": "RS256"})

    async def fetch(url: str) -> dict[str, object]:
        return {
            "issuer": "https://attacker.example.test",
            "jwks_uri": "https://attacker.example.test/keys",
        }

    monkeypatch.setattr(external_oidc, "_fetch_json", fetch)

    assert await ExternalOIDCValidator(_config().api.oidc).validate(_token(key)) is None


@pytest.mark.asyncio
async def test_external_oidc_refreshes_jwks_once_for_a_rotated_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = RSAKey.generate_key(2048, parameters={"kid": "key-old", "alg": "RS256"})
    new_key = RSAKey.generate_key(2048, parameters={"kid": "key-a", "alg": "RS256"})
    jwks_requests = 0

    async def fetch(url: str) -> dict[str, object]:
        nonlocal jwks_requests
        if url.endswith("openid-configuration"):
            return {
                "issuer": "https://keycloak.example.test/realms/home",
                "jwks_uri": "https://keycloak.example.test/keys",
            }
        jwks_requests += 1
        key = old_key if jwks_requests == 1 else new_key
        return {"keys": [key.as_dict(private=False)]}

    monkeypatch.setattr(external_oidc, "_fetch_json", fetch)

    claims = await ExternalOIDCValidator(_config().api.oidc).validate(_token(new_key))

    assert claims is not None
    assert jwks_requests == 2
