from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

VectorFilterScalar = str | int | float | bool


@dataclass(frozen=True)
class VectorDateRange:
    """Inclusive ISO-8601 bounds for a vector payload field."""

    from_value: str | None = None
    to_value: str | None = None


@dataclass(frozen=True)
class VectorSearchFilters:
    """Provider-neutral constraints applied before vector ``top_k`` when supported."""

    equals: Mapping[str, VectorFilterScalar] = field(default_factory=dict)
    any_of: Mapping[str, tuple[VectorFilterScalar, ...]] = field(default_factory=dict)
    booleans: Mapping[str, bool] = field(default_factory=dict)
    date_ranges: Mapping[str, VectorDateRange] = field(default_factory=dict)

    @property
    def fields(self) -> frozenset[str]:
        return frozenset(
            (*self.equals, *self.any_of, *self.booleans, *self.date_ranges)
        )

    @property
    def is_empty(self) -> bool:
        return not self.fields

    def only(self, fields: set[str] | frozenset[str]) -> "VectorSearchFilters":
        return self._select(fields, include=True)

    def without(self, fields: set[str] | frozenset[str]) -> "VectorSearchFilters":
        return self._select(fields, include=False)

    def matches(self, payload: Mapping[str, Any]) -> bool:
        for key, expected in self.equals.items():
            if payload.get(key) != expected:
                return False
        for key, expected in self.any_of.items():
            value = payload.get(key)
            if isinstance(value, (list, tuple, set, frozenset)):
                if not set(expected).intersection(value):
                    return False
            elif value not in expected:
                return False
        for key, expected in self.booleans.items():
            if bool(payload.get(key)) is not expected:
                return False
        for key, bounds in self.date_ranges.items():
            value = payload.get(key)
            if value is None:
                return False
            normalized = str(value)
            if bounds.from_value is not None and normalized < bounds.from_value:
                return False
            if bounds.to_value is not None and normalized > bounds.to_value:
                return False
        return True

    def _select(
        self, fields: set[str] | frozenset[str], *, include: bool
    ) -> "VectorSearchFilters":
        def selected(key: str) -> bool:
            return (key in fields) is include

        return VectorSearchFilters(
            equals={key: value for key, value in self.equals.items() if selected(key)},
            any_of={key: value for key, value in self.any_of.items() if selected(key)},
            booleans={key: value for key, value in self.booleans.items() if selected(key)},
            date_ranges={
                key: value for key, value in self.date_ranges.items() if selected(key)
            },
        )


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_batch_insert: bool = False
    supports_transactions: bool = False
    supports_ttl: bool = False
    supports_tags: bool = False
    supports_metadata_indexing: bool = False
    supports_graph_records: bool = False
    supports_decay_fields: bool = False
    supports_shared_scopes: bool = False
    supports_plugin_metadata: bool = False
    supports_audit_events: bool = False
    supports_handoff_records: bool = False
    supports_vector_filtering: bool = False
    vector_filter_fields: tuple[str, ...] = ()
