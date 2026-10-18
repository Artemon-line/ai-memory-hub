from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from memory.import_discovery import DISCOVERY_PROVIDERS, DiscoveryProvider, DiscoverySource
from memory.importer_compatibility import (
    ImporterCompatibility,
    PinValue,
    SourceClient,
)

_FIXTURE_DIR = Path("tests/fixtures/importers")
_FOCUSED_TEST_FILE = Path("tests/unit/test_importers.py")
_DISCOVERY_TEST_FILE = Path("tests/unit/test_import_discovery.py")


@dataclass(frozen=True)
class ReleaseContext:
    version: str
    generated_on: date


class CompatibilityMatrixValidator:
    def __init__(self, repository_root: Path, registered_importers: Iterable[str]) -> None:
        self._repository_root = repository_root
        self._registered_importers = frozenset(registered_importers)

    def validate(self, entries: Sequence[ImporterCompatibility]) -> tuple[str, ...]:
        errors: list[str] = []
        importer_counts = Counter(entry.importer.value for entry in entries)
        duplicates = sorted(name for name, count in importer_counts.items() if count != 1)
        if duplicates:
            errors.append(f"matrix entries must be unique: {', '.join(duplicates)}")

        matrix_names = frozenset(importer_counts)
        missing = sorted(self._registered_importers - matrix_names)
        extra = sorted(matrix_names - self._registered_importers)
        if missing:
            errors.append(f"registered importers missing from matrix: {', '.join(missing)}")
        if extra:
            errors.append(f"matrix entries without registered importers: {', '.join(extra)}")

        pin_counts = Counter(pin.client for entry in entries for pin in entry.pins)
        missing_clients = sorted(
            set(SourceClient) - set(pin_counts), key=lambda client: client.value
        )
        duplicate_clients = sorted(
            (client for client, count in pin_counts.items() if count != 1),
            key=lambda client: client.value,
        )
        if missing_clients:
            errors.append(
                "source clients missing version pins: "
                + ", ".join(client.value for client in missing_clients)
            )
        if duplicate_clients:
            errors.append(
                "source clients must have exactly one version pin: "
                + ", ".join(client.value for client in duplicate_clients)
            )

        pin_values = Counter(pin.value for entry in entries for pin in entry.pins)
        missing_pin_values = sorted(set(PinValue) - set(pin_values), key=lambda pin: pin.value)
        duplicate_pin_values = sorted(
            (pin for pin, count in pin_values.items() if count != 1),
            key=lambda pin: pin.value,
        )
        if missing_pin_values:
            errors.append(
                "unused typed pin values: " + ", ".join(pin.value for pin in missing_pin_values)
            )
        if duplicate_pin_values:
            errors.append(
                "typed pin values must be unique: "
                + ", ".join(pin.value for pin in duplicate_pin_values)
            )

        test_symbols = self._test_symbols(errors)
        for entry in entries:
            if not entry.pins:
                errors.append(f"{entry.importer.value}: at least one client pin is required")
            if not entry.fixtures:
                errors.append(f"{entry.importer.value}: at least one fixture is required")
            if not entry.tests:
                errors.append(f"{entry.importer.value}: at least one focused test is required")
            for fixture in entry.fixtures:
                path = self._repository_root / _FIXTURE_DIR / fixture.value
                if not path.is_file():
                    errors.append(
                        f"{entry.importer.value}: missing fixture: {path.relative_to(self._repository_root)}"
                    )
            for test in entry.tests:
                if test.value not in test_symbols:
                    errors.append(
                        f"{entry.importer.value}: missing symbol {test.value} in {_FOCUSED_TEST_FILE}"
                    )
        return tuple(errors)

    def _test_symbols(self, errors: list[str]) -> frozenset[str]:
        path = self._repository_root / _FOCUSED_TEST_FILE
        if not path.is_file():
            errors.append(f"missing focused test file: {_FOCUSED_TEST_FILE}")
            return frozenset()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return frozenset(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )


class DiscoveryMatrixValidator:
    def __init__(self, repository_root: Path, registered_importers: Iterable[str]) -> None:
        self._repository_root = repository_root
        self._registered_importers = frozenset(registered_importers)

    def validate(
        self,
        providers: Sequence[DiscoveryProvider],
        adapter_sources: Iterable[str],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        source_counts = Counter(provider.source for provider in providers)
        duplicates = sorted(
            (source.value for source, count in source_counts.items() if count != 1)
        )
        if duplicates:
            errors.append(f"discovery providers must be unique: {', '.join(duplicates)}")
        matrix_sources = {source.value for source in source_counts}
        expected_sources = {source.value for source in DiscoverySource}
        adapter_source_set = set(adapter_sources)
        if missing := sorted(expected_sources - matrix_sources):
            errors.append(f"discovery sources missing from matrix: {', '.join(missing)}")
        if missing := sorted(matrix_sources - adapter_source_set):
            errors.append(f"discovery sources missing adapters: {', '.join(missing)}")
        if extra := sorted(adapter_source_set - matrix_sources):
            errors.append(f"discovery adapters missing matrix entries: {', '.join(extra)}")

        test_symbols = self._test_symbols(errors)
        for provider in providers:
            if provider.importer not in self._registered_importers:
                errors.append(
                    f"{provider.source.value}: unknown importer: {provider.importer}"
                )
            if provider.focused_test not in test_symbols:
                errors.append(
                    f"{provider.source.value}: missing symbol {provider.focused_test} "
                    f"in {_DISCOVERY_TEST_FILE}"
                )
        return tuple(errors)

    def _test_symbols(self, errors: list[str]) -> frozenset[str]:
        path = self._repository_root / _DISCOVERY_TEST_FILE
        if not path.is_file():
            errors.append(f"missing discovery test file: {_DISCOVERY_TEST_FILE}")
            return frozenset()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return frozenset(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )


class CompatibilityMarkdownRenderer:
    def render(
        self,
        entries: Sequence[ImporterCompatibility],
        release: ReleaseContext,
        discovery_providers: Sequence[DiscoveryProvider] = DISCOVERY_PROVIDERS,
    ) -> str:
        ordered = sorted(entries, key=lambda entry: entry.importer.value)
        lines = [
            "# Importer Compatibility Matrix",
            "",
            "<!-- Generated during docs/release preparation; no checked-in copy exists. -->",
            "",
            f"Generated for ai-memory-hub `{release.version}` on {release.generated_on.isoformat()}.",
            "Every source client has exactly one required pin. Exact release pins are used only",
            "when fixture evidence identifies that release; otherwise the pin names the bounded",
            "format, rolling-service endpoint, repository contract, or user-authored contract.",
            "A pin is not a promise that unknown future client releases are compatible.",
            "",
            "## Client pins",
            "",
            "| Importer | Client | Pin type | Pin |",
            "| --- | --- | --- | --- |",
        ]
        for entry in ordered:
            for pin in sorted(entry.pins, key=lambda item: item.client.value):
                lines.append(
                    f"| `{entry.importer.value}` | {pin.client.value} | {pin.kind.value} | "
                    f"`{pin.value.value}` |"
                )

        lines.extend(
            (
                "",
                "## Latest-session discovery providers",
                "",
                "These providers back `aim import latest <source>`. Each row is exercised by",
                "the linked provider-matrix test before this report is generated.",
                "",
                "| Source | Importer | Environment override | Default root | Artifact pattern | Test |",
                "| --- | --- | --- | --- | --- | --- |",
            )
        )
        for provider in sorted(discovery_providers, key=lambda item: item.source.value):
            default_root = "~/" + "/".join(provider.default_home_parts)
            lines.append(
                f"| `{provider.source.value}` | `{provider.importer}` | "
                f"`{provider.environment_variable}` | `{default_root}` | "
                f"{provider.artifact_pattern} | "
                f"`{_DISCOVERY_TEST_FILE.as_posix()}::{provider.focused_test}` |"
            )

        lines.extend(("", "## Detailed evidence", ""))
        for entry in ordered:
            lines.extend(self._render_entry(entry))
        return "\n".join(lines).rstrip() + "\n"

    def _render_entry(self, entry: ImporterCompatibility) -> list[str]:
        pins = ", ".join(
            f"{pin.client.value} - `{pin.value.value}` ({pin.kind.value})" for pin in entry.pins
        )
        fixtures = ", ".join(
            f"`{(_FIXTURE_DIR / fixture.value).as_posix()}`" for fixture in entry.fixtures
        )
        tests = ", ".join(
            f"`{_FOCUSED_TEST_FILE.as_posix()}::{test.value}`" for test in entry.tests
        )
        return [
            f"### {entry.importer.value}",
            "",
            f"- **Client pins:** {pins}",
            f"- **Artifact or command:** {entry.artifact}",
            f"- **Default local location:** {entry.default_location}",
            f"- **Contract status:** {entry.contract.value}",
            f"- **Supported envelope:** {entry.supported_envelope}",
            f"- **Fixture evidence:** {fixtures}",
            f"- **Focused tests:** {tests}",
            f"- **Known omissions:** {entry.known_omissions}",
            f"- **Unknown-format fallback:** {entry.fallback}",
            "",
        ]
