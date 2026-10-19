from memory.api.connect_import import import_source_options
from memory.importer_compatibility import IMPORTER_COMPATIBILITY
from memory.importers.registry import get_importer


def test_import_source_options_map_friendly_names_to_registered_parsers() -> None:
    options = import_source_options()

    assert len(options) == len(IMPORTER_COMPATIBILITY)
    assert all(option["label"] and option["guidance"] for option in options)
    assert all("`" not in option["guidance"] for option in options)
    assert all(get_importer(option["parser"]) for option in options)
    assert {option["parser"] for option in options} == {
        entry.importer.value for entry in IMPORTER_COMPATIBILITY
    }
