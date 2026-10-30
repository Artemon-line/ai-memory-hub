from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__:
    from tools import import_to_memory
else:
    import import_to_memory


class PiSessionPathError(ValueError):
    pass


def normalize_session_path(raw_path: str) -> Path:
    session_path = Path(raw_path).expanduser()
    if not session_path.is_absolute():
        raise PiSessionPathError("Pi session path must be absolute")
    if session_path.suffix.lower() != ".jsonl":
        raise PiSessionPathError("Pi session path must name a .jsonl artifact")
    return session_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload one explicitly selected, closed Pi session through the shared importer."
    )
    parser.add_argument(
        "session_path",
        help="Absolute path reported by Pi's /session command before the session was closed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        session_path = normalize_session_path(args.session_path)
    except PiSessionPathError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    return import_to_memory.main(["pi-session-jsonl", str(session_path), "pi"])


if __name__ == "__main__":
    raise SystemExit(main())
