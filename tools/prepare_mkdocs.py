from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

DEFAULT_OUTPUT_DIR = ".docs-build"
ROOT_DOC_FILES = (
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)

# Python-Markdown's link processors repeatedly rescan bracket-heavy input. These
# limits keep docs builds bounded while leaving ample room for ordinary prose.
_MARKDOWN_LINK_PROCESSOR_COUNT = 3
_MAX_MARKDOWN_BRACKET_WORK_PER_BLOCK = 1_000_000
_MAX_MARKDOWN_BRACKET_WORK_PER_FILE = 2_000_000
_MKDOCS_MARKDOWN_SUFFIXES = (".markdown", ".mdown", ".mkdn", ".mkd", ".md")


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
    if _is_project_checkout(source_root):
        _write_importer_compatibility(source_root, output_dir)
    _validate_markdown_complexity(source_root, output_dir)


def _validate_markdown_complexity(source_root: Path, output_dir: Path) -> None:
    extensions, extension_configs = _markdown_configuration(source_root)
    markdown_paths = (
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name.endswith(_MKDOCS_MARKDOWN_SUFFIXES)
    )
    for markdown_path in sorted(markdown_paths):
        text = markdown_path.read_text(encoding="utf-8")
        relative_path = markdown_path.relative_to(output_dir).as_posix()
        file_work = 0

        for start_line, block in _markdown_prose_blocks(
            text,
            extensions,
            extension_configs,
        ):
            block_work = _markdown_bracket_work(block)
            if block_work > _MAX_MARKDOWN_BRACKET_WORK_PER_BLOCK:
                raise ValueError(
                    f"unsafe Markdown complexity in {relative_path}:{start_line}: "
                    f"estimated bracket scan work {block_work:,} exceeds the "
                    f"per-block limit {_MAX_MARKDOWN_BRACKET_WORK_PER_BLOCK:,}; "
                    "balance brackets, split the prose, or move bracket-heavy "
                    "literal content into a fenced code block"
                )
            file_work += block_work

        if file_work > _MAX_MARKDOWN_BRACKET_WORK_PER_FILE:
            raise ValueError(
                f"unsafe Markdown complexity in {relative_path}: estimated bracket "
                f"scan work {file_work:,} exceeds the per-file limit "
                f"{_MAX_MARKDOWN_BRACKET_WORK_PER_FILE:,}; balance brackets, split "
                "the content across files, or use fenced code blocks for literals"
            )


def _markdown_configuration(
    source_root: Path,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    config_path = source_root / "mkdocs.yml"
    if not config_path.is_file():
        try:
            import markdown  # noqa: F401
        except ModuleNotFoundError:
            return [], {}
        return ["pymdownx.highlight", "pymdownx.superfences"], {}

    try:
        from mkdocs.config import load_config
    except ModuleNotFoundError:
        return [], {}
    config = load_config(config_file=str(config_path))
    return list(config["markdown_extensions"]), dict(config["mdx_configs"] or {})


def _markdown_prose_blocks(
    text: str,
    extensions: list[str],
    extension_configs: dict[str, dict[str, Any]],
) -> list[tuple[int, str]]:
    lines = text.splitlines()
    if extensions:
        import markdown

        parser = markdown.Markdown(
            extensions=extensions,
            extension_configs=extension_configs,
        )
        for preprocessor in parser.preprocessors:
            lines = preprocessor.run(lines)

    blocks: list[tuple[int, str]] = []
    block_lines: list[str] = []
    block_start = 1

    def flush_block() -> None:
        nonlocal block_lines
        if block_lines:
            blocks.append((block_start, "\n".join(block_lines)))
            block_lines = []

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            flush_block()
        else:
            if not block_lines:
                block_start = line_number
            block_lines.append(line)

    flush_block()
    return blocks


def _markdown_bracket_work(text: str) -> int:
    openers: list[int] = []
    work = 0
    backslash_run = 0

    for index, character in enumerate(text):
        if character == "\\":
            backslash_run += 1
            continue

        escaped = backslash_run % 2 == 1
        backslash_run = 0
        if character == "[" and not escaped:
            openers.append(index)
        elif character == "]" and not escaped and openers:
            work += index - openers.pop()

    work += sum(len(text) - opener for opener in openers)
    return work * _MARKDOWN_LINK_PROCESSOR_COUNT


def _is_project_checkout(source_root: Path) -> bool:
    return (source_root / "pyproject.toml").is_file() and (
        source_root / "memory" / "importers" / "registry.py"
    ).is_file()


def _write_importer_compatibility(
    source_root: Path,
    output_dir: Path,
) -> None:
    from memory.import_discovery import DISCOVERY_PROVIDERS, build_discovery_service
    from memory.importer_compatibility import IMPORTER_COMPATIBILITY, ImporterId
    from memory.importer_compatibility_report import (
        CompatibilityMarkdownRenderer,
        CompatibilityMatrixValidator,
        DiscoveryMatrixValidator,
        ReleaseContext,
    )

    errors = CompatibilityMatrixValidator(
        source_root,
        (importer.value for importer in ImporterId),
    ).validate(IMPORTER_COMPATIBILITY)
    if errors:
        raise ValueError("invalid importer compatibility matrix:\n- " + "\n- ".join(errors))
    discovery_errors = DiscoveryMatrixValidator(
        source_root,
        (importer.value for importer in ImporterId),
    ).validate(DISCOVERY_PROVIDERS, build_discovery_service(environment={}).sources)
    if discovery_errors:
        raise ValueError(
            "invalid import discovery matrix:\n- " + "\n- ".join(discovery_errors)
        )

    version = _read_project_version(source_root / "pyproject.toml")
    context = ReleaseContext(version=version, generated_on=date.today())
    report = CompatibilityMarkdownRenderer().render(IMPORTER_COMPATIBILITY, context)
    (output_dir / "docs" / "importer_compatibility.md").write_text(
        report,
        encoding="utf-8",
        newline="\n",
    )


def _read_project_version(pyproject_path: Path) -> str:
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml is missing [project]")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml is missing project.version")
    return version


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
