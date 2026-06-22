from __future__ import annotations

import json
from pathlib import Path

import yaml

IGNORED_PARTS = {
    ".bruno-ci-data",
    ".bruno-ci-logs",
    ".bruno-oauth-ci-data",
    ".bruno-oauth-ci-logs",
    "node_modules",
    "reports",
}


def _is_generated(path: Path) -> bool:
    return bool(IGNORED_PARTS.intersection(path.parts))


def _load_yaml(path: Path) -> None:
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Invalid YAML in {path}: {exc}"
        raise SystemExit(msg) from exc


def _load_json(path: Path) -> None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {path}: {exc}"
        raise SystemExit(msg) from exc


def main() -> None:
    root = Path("tests/bruno")
    yaml_files = [
        path
        for path in list(root.rglob("*.yml")) + list(root.rglob("*.yaml"))
        if not _is_generated(path)
    ]
    yaml_files.extend(
        [
            Path(".github/workflows/bruno-integration.yml"),
            Path(".github/workflows/storage-providers.yml"),
            *Path("examples/storage-providers").rglob("*.yaml"),
        ]
    )
    json_files = [path for path in root.rglob("*.json") if not _is_generated(path)]

    for path in yaml_files:
        _load_yaml(path)

    for path in json_files:
        _load_json(path)

    print("yaml/json ok")


if __name__ == "__main__":
    main()
