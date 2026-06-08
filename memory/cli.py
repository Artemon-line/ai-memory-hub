from __future__ import annotations

import argparse
import json
from typing import Sequence

from memory.config import load_config
from memory.ingestion.tokenizer import tokenizer_diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-memory-hub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tokenizer_check = subparsers.add_parser(
        "tokenizer-check",
        help="Report whether the configured tokenizer encoding resolves through tiktoken.",
    )
    tokenizer_check.add_argument(
        "--config",
        default=None,
        help="Path to a config YAML file. Defaults to config.yaml.",
    )
    tokenizer_check.add_argument(
        "--encoding",
        default=None,
        help="Override tokenizer.encoding from config.",
    )
    tokenizer_check.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "tokenizer-check":
        return _tokenizer_check(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def _tokenizer_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    encoding = args.encoding or config.tokenizer.encoding
    diagnostics = tokenizer_diagnostics(encoding)

    if args.json:
        print(json.dumps(diagnostics, indent=2, sort_keys=True))
    else:
        print(f"encoding: {diagnostics['encoding']}")
        print(f"tokenizer_used: {diagnostics['tokenizer_used']}")
        print(f"available: {str(diagnostics['available']).lower()}")
        if diagnostics["cache_env"]:
            print(f"cache_env: {diagnostics['cache_env']}")
            print(f"cache_dir: {diagnostics['cache_dir']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
