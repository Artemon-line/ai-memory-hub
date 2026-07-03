from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEFAULT_OUTPUT_DIR = ".docs-build"
ROOT_DOC_FILES = (
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)


def prepare_docs(source_root: Path, output_dir: Path) -> None:
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()

    if source_root == output_dir or output_dir in source_root.parents:
        raise ValueError("output_dir must not be the repository root or its parent")

    if source_root in output_dir.parents:
        shutil.rmtree(output_dir, ignore_errors=True)
    elif output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_readme(source_root, output_dir)
    _copy_license(source_root, output_dir)
    _copy_root_docs(source_root, output_dir)
    _copy_docs(source_root, output_dir)


def _copy_readme(source_root: Path, output_dir: Path) -> None:
    readme = source_root / "README.md"
    target = output_dir / "index.md"
    target.write_text(readme.read_text(encoding="utf-8"), encoding="utf-8")


def _copy_license(source_root: Path, output_dir: Path) -> None:
    license_file = source_root / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, output_dir / "LICENSE")


def _copy_root_docs(source_root: Path, output_dir: Path) -> None:
    for filename in ROOT_DOC_FILES:
        source = source_root / filename
        if source.exists():
            shutil.copy2(source, output_dir / filename)


def _copy_docs(source_root: Path, output_dir: Path) -> None:
    docs_source = source_root / "docs"
    docs_target = output_dir / "docs"
    shutil.copytree(docs_source, docs_target)

    roadmap = docs_target / "roadmap.md"
    if roadmap.exists():
        text = roadmap.read_text(encoding="utf-8")
        text = text.replace("(../README.md)", "(../index.md)")
        roadmap.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the MkDocs source tree.")
    parser.add_argument(
        "--source-root",
        default=".",
        type=Path,
        help="Repository root containing README.md and docs/.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        type=Path,
        help="Generated MkDocs docs_dir.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    prepare_docs(args.source_root, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
