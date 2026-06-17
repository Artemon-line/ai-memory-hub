from __future__ import annotations

import json
from pathlib import Path

import yaml


def main() -> None:
    root = Path("tests/bruno")
    yaml_files = list(root.rglob("*.yml")) + list(root.rglob("*.yaml"))
    yaml_files.append(Path(".github/workflows/bruno-integration.yml"))
    json_files = list(root.rglob("*.json"))

    for path in yaml_files:
        yaml.safe_load(path.read_text(encoding="utf-8"))

    for path in json_files:
        json.loads(path.read_text(encoding="utf-8"))

    print("yaml/json ok")


if __name__ == "__main__":
    main()
