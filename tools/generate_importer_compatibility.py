from __future__ import annotations

import argparse
from pathlib import Path

from memory.importers.compatibility import IMPORTER_COMPATIBILITY
from memory.importers.compatibility_report import (
    CompatibilityMarkdownRenderer,
    CompatibilityMatrixValidator,
)
from memory.importers.registry import importer_names

_ROOT = Path(__file__).resolve().parents[1]
_REPORT = _ROOT / "docs" / "importer_compatibility.md"


def generate_report(*, check: bool) -> int:
    errors = CompatibilityMatrixValidator(_ROOT, importer_names()).validate(IMPORTER_COMPATIBILITY)
    if errors:
        raise ValueError("invalid importer compatibility matrix:\n- " + "\n- ".join(errors))

    rendered = CompatibilityMarkdownRenderer().render(IMPORTER_COMPATIBILITY)
    if check:
        if not _REPORT.is_file() or _REPORT.read_text(encoding="utf-8") != rendered:
            raise ValueError(
                "docs/importer_compatibility.md is stale; run "
                "`uv run python tools/generate_importer_compatibility.py`"
            )
        return 0

    _REPORT.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the importer compatibility report")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in report does not match the source records",
    )
    args = parser.parse_args()
    return generate_report(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
