from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, cast

from memory.config import OIDCResourceServerConfig

_MAX_PROVIDER_SUBJECT_LENGTH = 255
_MAX_USERNAME_LENGTH = 255
_MAX_EMAIL_LENGTH = 320
_MAX_DISPLAY_NAME_LENGTH = 1024


@dataclass(frozen=True)
class ExternalOIDCClaims:
    owner_id: str
    provider: str
    subject: str
    token_id: str | None
    scopes: frozenset[str]
    issuer: str
    username: str | None
    email: str | None
    display_name: str | None


class ExternalOIDCValidator:
    """Validate externally issued OIDC access tokens without persisting token material."""

    def __init__(self, config: OIDCResourceServerConfig) -> None:
        try:
            import joserfc  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "oidc_resource_server requires installing the oauth optional extra"
            ) from exc
        self._config = config
        self._metadata_cache: tuple[int, dict[str, object]] | None = None
        self._jwks_cache: tuple[int, dict[str, object]] | None = None
        self._readiness_cache: tuple[int, bool] | None = None

    async def validate(self, access_token: str) -> ExternalOIDCClaims | None:
        jwks = await self._jwks()
        if jwks is None:
            return None
        self._cache_readiness(True)
        claims = _decode_token(access_token, jwks, self._config.algorithms)
        if claims is None and not _jwks_contains_token_key(access_token, jwks):
            # A single refresh handles normal signing-key rotation without making every
            # invalid token trigger repeated provider requests.
            self._jwks_cache = None
            jwks = await self._jwks()
            claims = (
                _decode_token(access_token, jwks, self._config.algorithms)
                if jwks is not None
                else None
            )
        if claims is None or not self._validate_claims(claims):
            return None

        subject = str(claims["sub"]).strip()
        roles = _roles_from_claims(claims, self._config)
        scopes = _scopes_from_claims(claims)
        for role in roles:
            scopes.update(self._config.role_scopes.get(role, []))
        username = _optional_string_claim(
            claims, self._config.username_claim, max_length=_MAX_USERNAME_LENGTH
        )
        email = _optional_string_claim(
            claims, self._config.email_claim, max_length=_MAX_EMAIL_LENGTH
        )
        if claims.get("email_verified") is False:
            email = None
        display_name = _optional_string_claim(
            claims, self._config.display_name_claim, max_length=_MAX_DISPLAY_NAME_LENGTH
        )
        return ExternalOIDCClaims(
            owner_id=_qualified_owner_id(self._config.issuer, subject),
            provider=_qualified_provider_id(self._config.issuer),
            subject=subject,
            token_id=str(claims["jti"]) if claims.get("jti") is not None else None,
            scopes=frozenset(scopes),
            issuer=self._config.issuer,
            username=username,
            email=email,
            display_name=display_name or username,
        )

    async def readiness(self) -> dict[str, bool]:
        """Report provider availability without exposing provider response details."""
        now = int(time.time())
        if self._readiness_cache is not None and self._readiness_cache[0] > now:
            return {"provider_ready": self._readiness_cache[1]}
        provider_ready = await self._jwks() is not None
        self._cache_readiness(provider_ready)
        return {"provider_ready": provider_ready}

    def _cache_readiness(self, provider_ready: bool) -> None:
        ttl = self._config.cache_ttl_seconds if provider_ready else 30
        self._readiness_cache = (int(time.time()) + ttl, provider_ready)

    def _validate_claims(self, claims: dict[str, object]) -> bool:
        now = int(time.time())
        skew = self._config.clock_skew_seconds
        exp = claims.get("exp")
        if not isinstance(exp, int) or exp <= now - skew:
            return False
        nbf = claims.get("nbf")
        if isinstance(nbf, int) and nbf > now + skew:
            return False
        iat = claims.get("iat")
        if isinstance(iat, int) and iat > now + skew:
            return False
        if claims.get("iss") != self._config.issuer:
            return False
        if not _audience_matches(claims.get("aud"), self._expected_audience()):
            return False
        authorized_party = claims.get("azp")
        if (
            self._config.client_id
            and authorized_party is not None
            and authorized_party != self._config.client_id
        ):
            return False
        subject = claims.get("sub")
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or len(subject.strip()) > _MAX_PROVIDER_SUBJECT_LENGTH
        ):
            return False

        groups = _string_set(_claim_value(claims, self._config.groups_claim))
        roles = _roles_from_claims(claims, self._config)
        if self._config.allowed_groups and groups.isdisjoint(self._config.allowed_groups):
            return False
        if self._config.allowed_roles and roles.isdisjoint(self._config.allowed_roles):
            return False
        if self._config.allowed_domains:
            email = _claim_value(claims, self._config.email_claim)
            if not isinstance(email, str) or "@" not in email:
                return False
            if claims.get("email_verified") is False:
                return False
            domain = email.rsplit("@", 1)[1].lower()
            if domain not in self._config.allowed_domains:
                return False
        return True

    def _expected_audience(self) -> str:
        return self._config.audience or self._config.client_id

    async def _jwks(self) -> dict[str, object] | None:
        now = int(time.time())
        if self._jwks_cache is not None and self._jwks_cache[0] > now:
            return self._jwks_cache[1]
        jwks_url = self._config.jwks_url
        if not jwks_url:
            metadata = await self._metadata()
            if metadata is None or metadata.get("issuer") != self._config.issuer:
                return None
            discovered = metadata.get("jwks_uri")
            if not isinstance(discovered, str) or not _safe_oidc_url(discovered):
                return None
            jwks_url = discovered
        payload = await _fetch_json(jwks_url)
        if payload is None or not isinstance(payload.get("keys"), list):
            return None
        self._jwks_cache = (now + self._config.cache_ttl_seconds, payload)
        return payload

    async def _metadata(self) -> dict[str, object] | None:
        now = int(time.time())
        if self._metadata_cache is not None and self._metadata_cache[0] > now:
            return self._metadata_cache[1]
        payload = await _fetch_json(self._config.discovery_url)
        if payload is None:
            return None
        self._metadata_cache = (now + self._config.cache_ttl_seconds, payload)
        return payload


def _decode_token(
    access_token: str, jwks: dict[str, object], algorithms: list[str]
) -> dict[str, object] | None:
    try:
        from joserfc import jwt
        from joserfc.jwk import KeySet

        key_set = KeySet.import_key_set(cast(Any, jwks))
        token = jwt.decode(access_token, key_set, algorithms=algorithms)
    except Exception:
        return None
    claims = token.claims
    return dict(claims) if isinstance(claims, dict) else None


def _jwks_contains_token_key(access_token: str, jwks: dict[str, object]) -> bool:
    parts = access_token.split(".")
    if len(parts) != 3:
        return True
    try:
        raw = base64.urlsafe_b64decode((parts[0] + "=" * (-len(parts[0]) % 4)).encode("ascii"))
        header = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeEncodeError):
        return True
    if not isinstance(header, dict) or not isinstance(header.get("kid"), str):
        return True
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return True
    return any(isinstance(key, dict) and key.get("kid") == header["kid"] for key in keys)


async def _fetch_json(url: str) -> dict[str, object] | None:
    if not _safe_oidc_url(url):
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        if response.status_code >= 400:
            return None
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_oidc_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in {str(item) for item in value}
    return False


def _scopes_from_claims(claims: dict[str, object]) -> set[str]:
    scopes: set[str] = set()
    for claim_name in ("scope", "scp"):
        value = claims.get(claim_name)
        if isinstance(value, str):
            scopes.update(item for item in value.split() if item)
        elif isinstance(value, list):
            scopes.update(str(item) for item in value if str(item).strip())
    return scopes


def _roles_from_claims(
    claims: dict[str, object], config: OIDCResourceServerConfig
) -> set[str]:
    roles = _string_set(_claim_value(claims, config.roles_claim))
    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        roles.update(_string_set(realm_access.get("roles")))
    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict) and config.client_id:
        client_access = resource_access.get(config.client_id)
        if isinstance(client_access, dict):
            roles.update(_string_set(client_access.get("roles")))
    return roles


def _claim_value(claims: dict[str, object], path: str) -> object:
    value: object = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _string_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        return {str(item) for item in value if str(item).strip()}
    return set()


def _optional_string_claim(
    claims: dict[str, object], path: str, *, max_length: int
) -> str | None:
    value = _claim_value(claims, path)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= max_length else None


def _qualified_provider_id(issuer: str) -> str:
    digest = hashlib.sha256(issuer.encode()).hexdigest()
    return f"oidc_{digest[:32]}"


def _qualified_owner_id(issuer: str, subject: str) -> str:
    digest = hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()
    return f"oidc:{digest}"
