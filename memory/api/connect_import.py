from __future__ import annotations

from typing import TypedDict

from memory.importer_compatibility import IMPORTER_COMPATIBILITY


class ImportSourceOption(TypedDict):
    label: str
    parser: str
    guidance: str


def import_source_options() -> list[ImportSourceOption]:
    """Build the friendly chooser from the canonical importer compatibility data."""
    return [
        {
            "label": " / ".join(pin.client.value for pin in entry.pins),
            "parser": entry.importer.value,
            "guidance": entry.artifact.replace("`", ""),
        }
        for entry in IMPORTER_COMPATIBILITY
    ]
