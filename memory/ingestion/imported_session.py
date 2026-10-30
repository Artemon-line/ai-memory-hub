from __future__ import annotations

import hashlib
from typing import Any, Mapping

IMPORTED_SESSION_IDENTITY_KEY = "imported_session_identity"
IMPORTED_SESSION_IDENTITY_VERSION = "v1"


def derive_imported_session_identity(
    *, source: str, project_id: str, metadata: Mapping[str, Any]
) -> str | None:
    """Return a privacy-preserving identity for a registered history import."""
    parser = metadata.get("import_parser")
    source_session_id = metadata.get("source_session_id")
    if not isinstance(parser, str) or not parser.strip():
        return None
    if not isinstance(source_session_id, str) or not source_session_id.strip():
        return None

    canonical = "\0".join(
        (
            IMPORTED_SESSION_IDENTITY_VERSION,
            project_id.strip(),
            source.strip().casefold(),
            parser.strip().casefold(),
            source_session_id.strip(),
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def stamp_imported_session_identity(conversation: dict[str, Any]) -> str | None:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return None
    project_id = metadata.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    identity = derive_imported_session_identity(
        source=str(conversation.get("source", "")),
        project_id=project_id,
        metadata=metadata,
    )
    if identity is None:
        metadata.pop(IMPORTED_SESSION_IDENTITY_KEY, None)
        return None
    metadata[IMPORTED_SESSION_IDENTITY_KEY] = identity
    return identity
