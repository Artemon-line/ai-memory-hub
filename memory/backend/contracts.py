from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_batch_insert: bool = False
    supports_transactions: bool = False
    supports_ttl: bool = False
    supports_tags: bool = False
    supports_metadata_indexing: bool = False

