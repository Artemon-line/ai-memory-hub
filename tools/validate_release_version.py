from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

RELEASE_TAG_PATTERN = re.compile(
    r"^v(?P<version>\d+\.\d+\.\d+)(?P<prerelease>-rc\.(?P<rc>\d+))?$"
)


class ReleaseVersionError(ValueError):
    """Raised when a release tag does not match project version policy."""


@dataclass(frozen=True)
class ReleaseVersion:
    tag: str
    project_version: str
    version: str
    is_prerelease: bool


def read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as pyproject_file:
        data = tomllib.load(pyproject_file)
    project = data.get("project")
    if not isinstance(project, dict):
        raise ReleaseVersionError("pyproject.toml is missing a [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseVersionError("pyproject.toml is missing project.version")
    return version


def validate_release_tag(tag: str, project_version: str) -> ReleaseVersion:
    match = RELEASE_TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ReleaseVersionError(
            "release tag must use vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-rc.N"
        )
    version = match.group("version")
    if version != project_version:
        raise ReleaseVersionError(
            f"release tag {tag!r} targets version {version!r}, "
            f"but pyproject.toml has {project_version!r}"
        )
    rc = match.group("rc")
    if rc is not None and int(rc) < 1:
        raise ReleaseVersionError("release candidate number must be greater than zero")
    return ReleaseVersion(
        tag=tag,
        project_version=project_version,
        version=version,
        is_prerelease=match.group("prerelease") is not None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate that a release tag matches pyproject.toml."
    )
    parser.add_argument("tag", help="Release tag, such as v0.1.0 or v0.1.0-rc.1.")
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        type=Path,
        help="Path to pyproject.toml.",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="Print key=value lines suitable for appending to GITHUB_OUTPUT.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project_version = read_project_version(args.pyproject)
        release = validate_release_tag(args.tag, project_version)
    except ReleaseVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.github_output:
        print(f"tag={release.tag}")
        print(f"version={release.version}")
        print(f"is_prerelease={str(release.is_prerelease).lower()}")
    else:
        prerelease = "true" if release.is_prerelease else "false"
        print(
            f"release tag {release.tag} matches project version "
            f"{release.project_version} (prerelease={prerelease})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
