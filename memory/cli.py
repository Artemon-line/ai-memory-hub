from __future__ import annotations

import argparse
import json
from typing import Sequence

from memory.config import load_config
from memory.ingestion import mvp_ingestion
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

    fact_search = subparsers.add_parser(
        "fact-search",
        help="Review extracted facts from the configured metadata store.",
    )
    fact_search.add_argument("--config", default=None, help="Path to a config YAML file.")
    fact_search.add_argument("--subject", default=None, help="Filter by fact subject.")
    fact_search.add_argument("--predicate", default=None, help="Filter by fact predicate.")
    fact_search.add_argument(
        "--include-superseded",
        action="store_true",
        help="Include superseded facts in the review output.",
    )
    fact_search.add_argument("--json", action="store_true", help="Print JSON output.")

    profile_get = subparsers.add_parser(
        "profile-get",
        help="Review active profile facts for a subject.",
    )
    profile_get.add_argument("--config", default=None, help="Path to a config YAML file.")
    profile_get.add_argument("--subject", default="user", help="Profile subject to review.")
    profile_get.add_argument("--json", action="store_true", help="Print JSON output.")

    fact_supersede = subparsers.add_parser(
        "fact-supersede",
        help="Mark an extracted fact as superseded by another fact.",
    )
    fact_supersede.add_argument("--config", default=None, help="Path to a config YAML file.")
    fact_supersede.add_argument("fact_id", help="Fact id to supersede.")
    fact_supersede.add_argument("superseded_by", help="Replacement fact id.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "tokenizer-check":
        return _tokenizer_check(args)
    if args.command == "fact-search":
        return _fact_search(args)
    if args.command == "profile-get":
        return _profile_get(args)
    if args.command == "fact-supersede":
        return _fact_supersede(args)

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


def _configure_memory_runtime(config_path: str | None) -> None:
    config = load_config(config_path)
    mvp_ingestion.configure_runtime(config=config)


def _fact_search(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.fact_search(
        subject=args.subject,
        predicate=args.predicate,
        include_superseded=args.include_superseded,
    )
    _print_fact_result(result, json_output=args.json)
    return 0


def _profile_get(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.profile_get(subject=args.subject)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"subject: {result['subject']}")
        for fact in result.get("facts", []):
            print(f"- {fact['id']} {fact['predicate']}: {fact['object']}")
    return 0


def _fact_supersede(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.fact_supersede(
        fact_id=args.fact_id,
        superseded_by=args.superseded_by,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def _print_fact_result(result: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    for fact in result.get("results", []):
        if isinstance(fact, dict):
            print(f"- {fact['id']} {fact['subject']}.{fact['predicate']}: {fact['object']}")


if __name__ == "__main__":
    raise SystemExit(main())
