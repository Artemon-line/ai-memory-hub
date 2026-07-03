from pathlib import Path

from tools.validate_release_version import (
    ReleaseVersionError,
    read_project_version,
    validate_release_tag,
)


def test_validate_release_tag_accepts_stable_tag() -> None:
    release = validate_release_tag("v0.1.0", "0.1.0")

    assert release.tag == "v0.1.0"
    assert release.version == "0.1.0"
    assert release.is_prerelease is False


def test_validate_release_tag_accepts_release_candidate() -> None:
    release = validate_release_tag("v0.1.0-rc.1", "0.1.0")

    assert release.version == "0.1.0"
    assert release.is_prerelease is True


def test_validate_release_tag_rejects_mismatched_project_version() -> None:
    try:
        validate_release_tag("v0.2.0", "0.1.0")
    except ReleaseVersionError as exc:
        assert "pyproject.toml has '0.1.0'" in str(exc)
    else:
        raise AssertionError("expected ReleaseVersionError")


def test_validate_release_tag_rejects_unsupported_prerelease() -> None:
    try:
        validate_release_tag("v0.1.0-beta.1", "0.1.0")
    except ReleaseVersionError as exc:
        assert "vMAJOR.MINOR.PATCH" in str(exc)
    else:
        raise AssertionError("expected ReleaseVersionError")


def test_read_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "example"\nversion = "1.2.3"\n', encoding="utf-8")

    assert read_project_version(pyproject) == "1.2.3"
