from __future__ import annotations


class StorageError(RuntimeError):
    """Base storage-layer error."""


class NotSupportedError(StorageError):
    """Raised when an optional feature is not supported by a provider."""


class SchemaVersionError(StorageError):
    """Raised when metadata store schema version is incompatible."""


class VectorDimensionError(StorageError):
    """Raised when vector dimensionality does not match expected value."""

