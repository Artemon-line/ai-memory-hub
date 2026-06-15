from __future__ import annotations

from memory.importers.base import ConversationImporter
from memory.importers.manual_paste import ManualPasteImporter

_IMPORTERS: dict[str, ConversationImporter] = {
    ManualPasteImporter.name: ManualPasteImporter(),
}


def get_importer(name: str) -> ConversationImporter:
    try:
        return _IMPORTERS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(_IMPORTERS))
        raise ValueError(f"unknown importer: {name}; supported importers: {supported}") from exc


def importer_names() -> tuple[str, ...]:
    return tuple(sorted(_IMPORTERS))

