from __future__ import annotations

import hashlib
import hmac
import os

from memory.config import HubConfig

SECRET_HASH_ITERATIONS = 210_000
CONNECT_CSRF_HEADER = "x-ai-memory-hub-csrf"


def session_secret(config: HubConfig) -> str:
    return config.api.connect.session_secret or os.environ.get(
        config.api.connect.session_secret_env, ""
    )


def hash_secret(value: str, config: HubConfig, *, purpose: str) -> str:
    salt = f"ai-memory-hub:{purpose}:{session_secret(config)}".encode()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode(),
        salt,
        SECRET_HASH_ITERATIONS,
    ).hex()
    return "pbkdf2-sha256:" + digest


def csrf_matches(session: dict[str, object], csrf_token: str, *, config: HubConfig) -> bool:
    expected = str(session.get("csrf_token_hash") or "")
    actual = hash_secret(csrf_token, config, purpose="connect-csrf")
    return bool(csrf_token) and hmac.compare_digest(expected, actual)
