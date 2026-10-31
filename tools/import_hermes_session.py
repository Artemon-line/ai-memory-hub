from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from tools import import_to_memory
else:
    import import_to_memory


_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AGE_PATTERN = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?[hdw]?$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export closed Hermes sessions privately and upload the canonical JSONL."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--session-id", help="Exact ID or unique prefix of one ended session.")
    target.add_argument(
        "--ended-older-than",
        metavar="AGE",
        help="Export ended sessions older than an age such as 5h or 2d.",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="Ask Hermes to redact recognized credentials from the private export.",
    )
    parser.add_argument("--hermes-command", default="hermes", help=argparse.SUPPRESS)
    return parser


def _export_arguments(args: argparse.Namespace, artifact: Path) -> list[str]:
    command = [args.hermes_command, "sessions", "export", str(artifact)]
    if args.session_id is not None:
        if not _SESSION_ID_PATTERN.fullmatch(args.session_id):
            raise ValueError("Hermes session ID is invalid")
        command.extend(["--session-id", args.session_id])
    else:
        if not _AGE_PATTERN.fullmatch(args.ended_older_than):
            raise ValueError("Hermes ended-session age is invalid")
        command.extend(["--older-than", args.ended_older_than])
    if args.redact:
        command.append("--redact")
    return command


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="ai-memory-hermes-") as directory:
            artifact = Path(directory) / "hermes-sessions.jsonl"
            command = _export_arguments(args, artifact)
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                print("import failed: Hermes export command could not run", file=sys.stderr)
                return 1
            if result.returncode != 0:
                print("import failed: Hermes export command failed", file=sys.stderr)
                return 1
            if not artifact.is_file():
                print("import failed: Hermes export produced no JSONL artifact", file=sys.stderr)
                return 1
            return import_to_memory.main(
                [
                    "hermes-session-jsonl",
                    str(artifact),
                    "--min-age-seconds",
                    "0",
                ]
            )
    except ValueError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
