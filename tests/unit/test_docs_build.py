from pathlib import Path

import pytest

from tools.prepare_mkdocs import prepare_docs


def _docs_source(tmp_path: Path, readme: str = "# Project\n") -> Path:
    source_root = tmp_path / "repo"
    (source_root / "docs").mkdir(parents=True)
    (source_root / "README.md").write_text(readme, encoding="utf-8")
    return source_root


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


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 900,
        "![" * 900,
        "[^" * 900,
        "[a][" * 900,
        "![[" * 900,
        "[" * 900 + "x" + "]" * 900,
        "```text\n" + "[" * 900,
        "~~~ bad header!\n" + "[" * 900 + "\n~~~\n",
        "```text\n" + "[" * 900 + "\n````\n",
    ],
)
def test_prepare_docs_rejects_pathological_bracket_scans(
    tmp_path: Path,
    payload: str,
) -> None:
    source_root = _docs_source(tmp_path, payload)

    with pytest.raises(ValueError, match=r"unsafe Markdown complexity in index\.md"):
        prepare_docs(source_root, tmp_path / "site-src")


def test_prepare_docs_rejects_multiline_and_fragmented_scan_work(tmp_path: Path) -> None:
    multiline = "\n".join("[" * 100 for _ in range(9))
    fragmented = "\n\n".join("[" * 400 for _ in range(9))

    for case, payload in enumerate((multiline, fragmented)):
        case_root = tmp_path / str(case)
        source_root = _docs_source(case_root, payload)
        with pytest.raises(ValueError, match=r"unsafe Markdown complexity in index\.md"):
            prepare_docs(source_root, case_root / "site-src")


@pytest.mark.parametrize("suffix", [".markdown", ".mdown", ".mkdn", ".mkd", ".md"])
def test_prepare_docs_checks_every_mkdocs_page_suffix(
    tmp_path: Path,
    suffix: str,
) -> None:
    source_root = _docs_source(tmp_path)
    nested_page = source_root / "docs" / "nested" / f"attack{suffix}"
    nested_page.parent.mkdir()
    nested_page.write_text("[" * 900, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"unsafe Markdown complexity in docs/nested/attack",
    ):
        prepare_docs(source_root, tmp_path / "site-src")


def test_prepare_docs_allows_normal_markdown_and_fenced_literals(tmp_path: Path) -> None:
    readme = (
        "# Project\n\n"
        + "[documentation](https://example.com)\n" * 1_000
        + r"\[escaped literal\]" * 1_000
        + "\n\n```text\n"
        + "[" * 2_000
        + "\n```\n"
        + "\n> ```text\n> "
        + "[" * 2_000
        + "\n> ```\n"
    )
    source_root = _docs_source(tmp_path, readme)

    prepare_docs(source_root, tmp_path / "site-src")


def test_published_docs_default_to_releases_and_keep_main_experimental() -> None:
    config = Path("mkdocs.yml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
    warning = Path("docs/overrides/main.html").read_text(encoding="utf-8")

    assert "site_url: https://artemon-line.github.io/ai-memory-hub/" in config
    assert "provider: mike" in config
    assert "default: latest" in config
    assert "canonical_version: latest" in config
    assert "release:" in workflow
    assert 'mike deploy --push --title "main (experimental)" main' in workflow
    assert 'mike set-default --push latest' in workflow
    assert "experimental,\n    unreleased, or untested behavior" in warning


def test_docs_flag_and_version_banner_markup_are_browser_safe() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    config = Path("mkdocs.yml").read_text(encoding="utf-8")
    outdated_script = Path(
        "docs/overrides/partials/javascripts/outdated.html"
    ).read_text(encoding="utf-8")

    assert 'alt="Ukraine flag" width="18" height="14"' in readme
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in config
    assert 'src="docs/assets/flags/ua.svg"' not in config
    assert 'new URL("{{ base_url }}",location)' in outdated_script
