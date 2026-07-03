from pathlib import Path

import pytest

from tools.prepare_mkdocs import prepare_docs


def test_prepare_docs_uses_readme_as_home_and_rewrites_root_link(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    docs_dir = source_root / "docs"
    docs_dir.mkdir(parents=True)
    (source_root / "README.md").write_text("# Project\n\nSee [Docs](docs/roadmap.md).\n", encoding="utf-8")
    (source_root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (source_root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (docs_dir / "roadmap.md").write_text(
        "# Roadmap\n\n[Project overview](../README.md)\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "site-src"
    prepare_docs(source_root, output_dir)

    assert (output_dir / "index.md").read_text(encoding="utf-8").startswith("# Project")
    assert (output_dir / "LICENSE").read_text(encoding="utf-8") == "MIT\n"
    assert (output_dir / "CHANGELOG.md").read_text(encoding="utf-8") == "# Changelog\n"
    assert "(../index.md)" in (output_dir / "docs" / "roadmap.md").read_text(encoding="utf-8")


def test_prepare_docs_rejects_repo_root_as_output(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    (source_root / "docs").mkdir(parents=True)
    (source_root / "README.md").write_text("# Project\n", encoding="utf-8")

    with pytest.raises(ValueError, match="repository root"):
        prepare_docs(source_root, source_root)
