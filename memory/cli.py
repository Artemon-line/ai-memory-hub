from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import jsonschema

from memory.backend.log_safety import redact_secrets
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig, load_config
from memory.ingestion import mvp_ingestion
from memory.ingestion.tokenizer import tokenizer_diagnostics
from memory.interfaces.mcp_server import _apply_search_filters


EXIT_OK = 0
EXIT_COMMAND_FAILURE = 1
EXIT_USAGE = 2
EXIT_RUNTIME = 3


class UsageError(Exception):
    pass


class CLIArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = CLIArgumentParser(prog="ai-memory-hub")
    parser.add_argument("--config", default=None, help="Path to a config YAML file.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-essential text output.")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostic details for failures.")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=CLIArgumentParser)

    tokenizer_check = subparsers.add_parser(
        "tokenizer-check",
        help="Report whether the configured tokenizer encoding resolves through tiktoken.",
    )
    _add_common_options(tokenizer_check)
    tokenizer_check.add_argument("--encoding", default=None, help="Override tokenizer.encoding from config.")

    ingest = subparsers.add_parser("ingest", help="Ingest a conversation JSON file.")
    _add_common_options(ingest)
    ingest.add_argument("file", help="Conversation JSON file path, or '-' for stdin.")
    ingest.add_argument(
        "--strict-transcript",
        action="store_true",
        help="Treat string input as a strict User:/Assistant: raw transcript.",
    )

    search = subparsers.add_parser("search", help="Search stored memory.")
    _add_common_options(search)
    search.add_argument("query", help="Search query.")
    search.add_argument("--top-k", type=int, default=5, help="Number of results to return.")
    search.add_argument("--source", default=None, help="Filter results by conversation source.")
    search.add_argument("--date-from", default=None, help="Filter results from this ISO-8601 timestamp.")
    search.add_argument("--date-to", default=None, help="Filter results through this ISO-8601 timestamp.")
    search.add_argument("--tags", action="append", default=None, help="Require a tag. Repeat for multiple tags.")
    search.add_argument(
        "--result-mode",
        choices=["chunks", "compact", "conversations"],
        default="chunks",
        help="Result shape to return.",
    )

    retrieve = subparsers.add_parser("retrieve", help="Retrieve a stored conversation by id.")
    _add_common_options(retrieve)
    retrieve.add_argument("id", help="Memory id to retrieve.")

    ask = subparsers.add_parser("ask", help="Ask a question using stored memory.")
    _add_common_options(ask)
    ask.add_argument("question", help="Question to answer.")
    ask.add_argument("--top-k", type=int, default=5, help="Number of memories to retrieve.")
    ask.add_argument("--max-context-tokens", type=int, default=None, help="Optional context token budget.")
    ask.add_argument(
        "--result-mode",
        choices=["chunks", "compact", "conversations"],
        default="chunks",
        help="Result shape to return.",
    )

    health = subparsers.add_parser("health", help="Initialize runtime and report provider health.")
    _add_common_options(health)

    config_show = subparsers.add_parser("config-show", help="Print normalized config with secrets redacted.")
    _add_common_options(config_show)

    storage_check = subparsers.add_parser("storage-check", help="Report metadata/vector provider capabilities.")
    _add_common_options(storage_check)

    serve = subparsers.add_parser("serve", help="Start the FastAPI and MCP service.")
    _add_common_options(serve)
    serve.add_argument("--host", default=None, help="Host to bind. Defaults to api.host from config.")
    serve.add_argument("--port", type=int, default=None, help="Port to bind. Defaults to api.port from config.")

    fact_search = subparsers.add_parser("fact-search", help="Review extracted facts.")
    _add_common_options(fact_search)
    fact_search.add_argument("--subject", default=None, help="Filter by fact subject.")
    fact_search.add_argument("--predicate", default=None, help="Filter by fact predicate.")
    fact_search.add_argument("--include-superseded", action="store_true", help="Include superseded facts.")

    profile_get = subparsers.add_parser("profile-get", help="Review active profile facts for a subject.")
    _add_common_options(profile_get)
    profile_get.add_argument("--subject", default="user", help="Profile subject to review.")

    fact_supersede = subparsers.add_parser("fact-supersede", help="Mark a fact as superseded.")
    _add_common_options(fact_supersede)
    fact_supersede.add_argument("fact_id", help="Fact id to supersede.")
    fact_supersede.add_argument("superseded_by", help="Replacement fact id.")

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        args = argparse.Namespace(json=False, quiet=False, verbose=False)
        return _emit_error(args, "usage_error", str(exc), exit_code=EXIT_USAGE)
    _normalize_common_args(args)
    try:
        return _dispatch(args)
    except json.JSONDecodeError as exc:
        return _emit_error(args, "invalid_json", f"invalid JSON: {exc}")
    except jsonschema.ValidationError as exc:
        return _emit_error(args, "validation_error", exc.message)
    except ValueError as exc:
        return _emit_error(args, "validation_error", str(exc))
    except FileNotFoundError as exc:
        return _emit_error(args, "not_found", str(exc))
    except RuntimeError as exc:
        return _emit_error(args, "runtime_error", str(exc), exit_code=EXIT_RUNTIME)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}" if args.verbose else str(exc)
        return _emit_error(args, "runtime_error", message, exit_code=EXIT_RUNTIME)


def _normalize_common_args(args: argparse.Namespace) -> None:
    for name, default in (("config", None), ("json", False), ("quiet", False), ("verbose", False)):
        if not hasattr(args, name):
            setattr(args, name, default)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "tokenizer-check":
        return _tokenizer_check(args)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "search":
        return _search(args)
    if args.command == "retrieve":
        return _retrieve(args)
    if args.command == "ask":
        return _ask(args)
    if args.command == "health":
        return _health(args)
    if args.command == "config-show":
        return _config_show(args)
    if args.command == "storage-check":
        return _storage_check(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "fact-search":
        return _fact_search(args)
    if args.command == "profile-get":
        return _profile_get(args)
    if args.command == "fact-supersede":
        return _fact_supersede(args)
    raise ValueError(f"unknown command: {args.command}")


def _tokenizer_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    encoding = args.encoding or config.tokenizer.encoding
    diagnostics = tokenizer_diagnostics(encoding)
    if args.json:
        _print_json(diagnostics)
    elif not args.quiet:
        print(f"encoding: {diagnostics['encoding']}")
        print(f"tokenizer_used: {diagnostics['tokenizer_used']}")
        print(f"available: {str(diagnostics['available']).lower()}")
        if diagnostics["cache_env"]:
            print(f"cache_env: {diagnostics['cache_env']}")
            print(f"cache_dir: {diagnostics['cache_dir']}")
    return EXIT_OK


def _ingest(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    payload = _read_payload(args.file)
    result = mvp_ingestion.ingest_messages(payload, strict_transcript=args.strict_transcript)
    result = redact_content_hashes(result)
    _emit_result(args, result, text_formatter=_format_ingest_text)
    return EXIT_OK


def _search(args: argparse.Namespace) -> int:
    _validate_top_k(args.top_k)
    _configure_memory_runtime(args.config)
    retrieval_k = 100 if _has_search_filters(args) else args.top_k
    result = redact_content_hashes(
        mvp_ingestion.search(args.query, top_k=retrieval_k, result_mode=args.result_mode)
    )
    result["results"] = _filter_search_results(result.get("results", []), args)[: args.top_k]
    _emit_result(args, result, text_formatter=_format_search_text)
    return EXIT_OK


def _retrieve(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    memory = mvp_ingestion.retrieve(args.id)
    if memory is None:
        result = _envelope(status="not_found", id=args.id, error_code="not_found", error_message="memory not found")
        _emit_result(args, result, text_formatter=lambda item: item["error_message"])
        return EXIT_COMMAND_FAILURE
    result = _envelope(status="ok", id=args.id, memory=redact_content_hashes(memory))
    _emit_result(args, result, text_formatter=_format_retrieve_text)
    return EXIT_OK


def _ask(args: argparse.Namespace) -> int:
    _validate_top_k(args.top_k)
    if args.max_context_tokens is not None and args.max_context_tokens < 1:
        raise ValueError("max_context_tokens must be a positive integer")
    _configure_memory_runtime(args.config)
    result = redact_content_hashes(
        mvp_ingestion.ask(
            args.question,
            top_k=args.top_k,
            max_context_tokens=args.max_context_tokens,
            result_mode=args.result_mode,
        )
    )
    _emit_result(args, result, text_formatter=_format_ask_text)
    return EXIT_OK


def _health(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = _envelope(status="ok", health=redact_content_hashes(mvp_ingestion.runtime_health()))
    _emit_result(args, result, text_formatter=_format_health_text)
    return EXIT_OK


def _config_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = _envelope(status="ok", config=_redact_config(config))
    _emit_result(args, result, text_formatter=lambda item: json.dumps(item["config"], indent=2, sort_keys=True))
    return EXIT_OK


def _storage_check(args: argparse.Namespace) -> int:
    runtime = _configure_memory_runtime(args.config)
    metadata_capabilities = _capabilities(runtime.metadata_store)
    vector_capabilities = _capabilities(runtime.vector_store)
    result = _envelope(
        status="ok",
        metadata=runtime.metadata_store.health() if hasattr(runtime.metadata_store, "health") else {},
        vector=runtime.vector_store.health() if hasattr(runtime.vector_store, "health") else {},
        capabilities={
            "metadata": metadata_capabilities,
            "vector": vector_capabilities,
        },
    )
    _emit_result(args, redact_content_hashes(result), text_formatter=_format_storage_text)
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    host = args.host or config.api.host
    port = args.port or config.api.port
    app = _create_cli_app(config)
    if not args.quiet:
        print(f"serving ai-memory-hub on {host}:{port}")
    _run_server(app, host=host, port=int(port))
    return EXIT_OK


def _create_cli_app(config: HubConfig) -> Any:
    from memory.api.server import create_app

    return create_app(config=config)


def _run_server(app: Any, *, host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(str(exc)) from exc
    uvicorn.run(app, host=host, port=int(port))


def _fact_search(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.fact_search(
        subject=args.subject,
        predicate=args.predicate,
        include_superseded=args.include_superseded,
    )
    _emit_result(args, redact_content_hashes(result), text_formatter=_format_fact_search_text)
    return EXIT_OK


def _profile_get(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.profile_get(subject=args.subject)
    _emit_result(args, redact_content_hashes(result), text_formatter=_format_profile_text)
    return EXIT_OK


def _fact_supersede(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.fact_supersede(fact_id=args.fact_id, superseded_by=args.superseded_by)
    _emit_result(args, redact_content_hashes(result), text_formatter=lambda item: item["status"])
    return EXIT_OK if result["status"] == "ok" else EXIT_COMMAND_FAILURE


def _configure_memory_runtime(config_path: str | None) -> mvp_ingestion.RuntimeDependencies:
    config = load_config(config_path)
    return mvp_ingestion.configure_runtime(config=config)


def _read_payload(path: str) -> Any:
    if path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(text)
    return text


def _validate_top_k(value: int) -> None:
    if value < 1 or value > 100:
        raise ValueError("top_k must be an integer between 1 and 100")


def _has_search_filters(args: argparse.Namespace) -> bool:
    return bool(args.source or args.date_from or args.date_to or args.tags)


def _filter_search_results(rows: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    typed_rows = [row for row in rows if isinstance(row, dict)]
    if not _has_search_filters(args):
        return typed_rows
    return _apply_search_filters(
        typed_rows,
        source=args.source,
        date_from=args.date_from,
        date_to=args.date_to,
        tags=args.tags,
    )


def _emit_result(
    args: argparse.Namespace,
    result: dict[str, Any],
    *,
    text_formatter: Any,
) -> None:
    if args.json:
        _print_json(result)
    elif not args.quiet:
        text = text_formatter(result)
        if text:
            print(text)


def _emit_error(
    args: argparse.Namespace,
    error_code: str,
    error_message: str,
    *,
    exit_code: int = EXIT_COMMAND_FAILURE,
) -> int:
    result = _envelope(
        status="error",
        error_code=error_code,
        error_message=redact_secrets(error_message),
    )
    if args.json:
        _print_json(result)
    elif not args.quiet:
        print(f"{error_code}: {result['error_message']}", file=sys.stderr)
    return exit_code


def _envelope(
    *,
    status: str,
    id: str | None = None,
    results: list[Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "id": id,
        "results": results if results is not None else [],
        "error_code": error_code,
        "error_message": error_message,
    }
    payload.update(extra)
    return payload


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _format_ingest_text(result: dict[str, Any]) -> str:
    return f"stored {result.get('id')} chunks={result.get('chunks', 0)} status={result.get('status')}"


def _format_search_text(result: dict[str, Any]) -> str:
    lines = []
    for row in result.get("results", []):
        lines.append(f"- {row.get('id')}#{row.get('chunk_index', 0)} score={row.get('score')}: {row.get('text', '')}")
    return "\n".join(lines) if lines else "no results"


def _format_retrieve_text(result: dict[str, Any]) -> str:
    memory = result.get("memory", {})
    messages = memory.get("messages", []) if isinstance(memory, dict) else []
    return f"{result.get('id')} messages={len(messages)} source={memory.get('source') if isinstance(memory, dict) else ''}"


def _format_ask_text(result: dict[str, Any]) -> str:
    lines = [str(result.get("answer", ""))]
    citations = result.get("citations", [])
    if citations:
        lines.append("citations:")
        for citation in citations:
            if isinstance(citation, dict):
                label = citation.get("fact_id") or f"{citation.get('id')}#{citation.get('chunk_index', 0)}"
                lines.append(f"- {label}")
    return "\n".join(line for line in lines if line)


def _format_health_text(result: dict[str, Any]) -> str:
    health = result.get("health", {})
    return f"status={result.get('status')} mode={health.get('mode') if isinstance(health, dict) else ''}"


def _format_storage_text(result: dict[str, Any]) -> str:
    metadata = result.get("metadata", {})
    vector = result.get("vector", {})
    return (
        f"metadata={metadata.get('provider') if isinstance(metadata, dict) else ''} "
        f"vector={vector.get('provider') if isinstance(vector, dict) else ''}"
    )


def _format_fact_search_text(result: dict[str, Any]) -> str:
    lines = []
    for fact in result.get("results", []):
        if isinstance(fact, dict):
            lines.append(f"- {fact['id']} {fact['subject']}.{fact['predicate']}: {fact['object']}")
    return "\n".join(lines) if lines else "no facts"


def _format_profile_text(result: dict[str, Any]) -> str:
    lines = [f"subject: {result['subject']}"]
    for fact in result.get("facts", []):
        if isinstance(fact, dict):
            lines.append(f"- {fact['id']} {fact['predicate']}: {fact['object']}")
    return "\n".join(lines)


def _capabilities(store: Any) -> dict[str, Any]:
    if not hasattr(store, "capabilities"):
        return {}
    caps = store.capabilities()
    if hasattr(caps, "__dict__"):
        return dict(caps.__dict__)
    if hasattr(caps, "model_dump"):
        return caps.model_dump()
    return {}


def _redact_config(config: HubConfig) -> dict[str, Any]:
    data = config.model_dump(by_alias=True)
    for section, key in (("openai", "api_key"), ("api", "api_key"), ("providers", "metadata_dsn")):
        value = data.get(section, {}).get(key)
        if value:
            data[section][key] = "***"
    return data


if __name__ == "__main__":
    raise SystemExit(main())
