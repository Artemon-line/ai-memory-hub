from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path
from typing import Any, NoReturn, Sequence

import jsonschema

from memory.backend.log_safety import redact_secrets
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig, load_config
from memory.importers import get_importer
from memory.ingestion import mvp_ingestion
from memory.ingestion.thread_models import SearchResultMode
from memory.ingestion.tokenizer import tokenizer_diagnostics
from memory.provider_models import (
    MetadataProviderConfigKey,
    ProviderConfigSection,
    SecretConfigKey,
    VectorProviderConfigKey,
)

EXIT_OK = 0
EXIT_COMMAND_FAILURE = 1
EXIT_USAGE = 2
_STORAGE_CONFIG_KEY = "storage"
_PROVIDER_SECRET_PATHS = (
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.PGVECTOR.value,
        SecretConfigKey.URL.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.QDRANT.value,
        SecretConfigKey.API_KEY.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.MILVUS.value,
        SecretConfigKey.TOKEN.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.WEAVIATE.value,
        SecretConfigKey.API_KEY.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.MONGODB_ATLAS.value,
        SecretConfigKey.URI.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.ELASTICSEARCH.value,
        SecretConfigKey.USERNAME.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.ELASTICSEARCH.value,
        SecretConfigKey.PASSWORD.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.OPENSEARCH.value,
        SecretConfigKey.USERNAME.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.OPENSEARCH.value,
        SecretConfigKey.PASSWORD.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.REDIS.value,
        SecretConfigKey.URL.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.PINECONE.value,
        SecretConfigKey.API_KEY.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.TURBOPUFFER.value,
        SecretConfigKey.API_KEY.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.VESPA.value,
        SecretConfigKey.TOKEN.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.VECTOR_PROVIDERS.value,
        VectorProviderConfigKey.TYPESENSE.value,
        SecretConfigKey.API_KEY.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.METADATA_PROVIDERS.value,
        MetadataProviderConfigKey.POSTGRES.value,
        SecretConfigKey.URL.value,
    ),
    (
        _STORAGE_CONFIG_KEY,
        ProviderConfigSection.METADATA_PROVIDERS.value,
        MetadataProviderConfigKey.MONGODB.value,
        SecretConfigKey.URI.value,
    ),
)
EXIT_RUNTIME = 3


class UsageError(Exception):
    pass


class CLIArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
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

    import_command = subparsers.add_parser(
        "import", help="Import an external conversation format."
    )
    _add_common_options(import_command)
    import_subparsers = import_command.add_subparsers(
        dest="importer", required=True, parser_class=CLIArgumentParser
    )
    manual_import = import_subparsers.add_parser(
        "manual",
        help="Import a pasted speaker-labelled transcript.",
    )
    _add_common_options(manual_import)
    manual_import.add_argument("file", help="Transcript file path, or '-' for stdin.")
    manual_import.add_argument(
        "--source",
        default="manual-paste",
        help="Conversation source stored with the imported transcript.",
    )
    manual_import.add_argument("--title", default=None, help="Optional conversation title.")

    search = subparsers.add_parser("search", help="Search stored memory.")
    _add_common_options(search)
    search.add_argument("query", help="Search query.")
    search.add_argument("--top-k", type=int, default=5, help="Number of results to return.")
    search.add_argument("--source", default=None, help="Filter results by conversation source.")
    search.add_argument("--date-from", default=None, help="Filter results from this ISO-8601 timestamp.")
    search.add_argument("--date-to", default=None, help="Filter results through this ISO-8601 timestamp.")
    search.add_argument("--tags", action="append", default=None, help="Require a tag. Repeat for multiple tags.")
    search.add_argument("--thread-id", default=None, help="Filter results by canonical thread id.")
    search.add_argument(
        "--result-mode",
        choices=[mode.value for mode in SearchResultMode],
        default=SearchResultMode.CHUNKS.value,
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
    ask.add_argument("--source", default=None, help="Filter context by conversation source.")
    ask.add_argument("--date-from", default=None, help="Filter context from this ISO-8601 timestamp.")
    ask.add_argument("--date-to", default=None, help="Filter context through this ISO-8601 timestamp.")
    ask.add_argument("--tags", action="append", default=None, help="Require a tag. Repeat for multiple tags.")
    ask.add_argument("--thread-id", default=None, help="Filter context by canonical thread id.")
    ask.add_argument(
        "--result-mode",
        choices=[mode.value for mode in SearchResultMode],
        default=SearchResultMode.CHUNKS.value,
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
    _add_fact_filter_options(fact_search, include_predicate=False)

    profile_get = subparsers.add_parser("profile-get", help="Review active profile facts for a subject.")
    _add_common_options(profile_get)
    profile_get.add_argument("--subject", default="user", help="Profile subject to review.")
    _add_fact_filter_options(profile_get, include_predicate=True)

    fact_supersede = subparsers.add_parser("fact-supersede", help="Mark a fact as superseded.")
    _add_common_options(fact_supersede)
    fact_supersede.add_argument("fact_id", help="Fact id to supersede.")
    fact_supersede.add_argument("superseded_by", help="Replacement fact id.")

    admin = subparsers.add_parser("admin", help="Manage local users, tokens, and projects.")
    _add_common_options(admin)
    admin_subparsers = admin.add_subparsers(
        dest="admin_resource", required=True, parser_class=CLIArgumentParser
    )

    admin_user = admin_subparsers.add_parser("user", help="Manage users/principals.")
    _add_common_options(admin_user)
    admin_user_subparsers = admin_user.add_subparsers(
        dest="admin_action", required=True, parser_class=CLIArgumentParser
    )
    admin_user_create = admin_user_subparsers.add_parser("create", help="Create or update a user.")
    _add_common_options(admin_user_create)
    admin_user_create.add_argument("user_id", help="User/principal id.")
    admin_user_create.add_argument("--display-name", default=None, help="Optional display name.")
    admin_user_list = admin_user_subparsers.add_parser("list", help="List users/principals.")
    _add_common_options(admin_user_list)

    admin_token = admin_subparsers.add_parser("token", help="Manage bearer tokens.")
    _add_common_options(admin_token)
    admin_token_subparsers = admin_token.add_subparsers(
        dest="admin_action", required=True, parser_class=CLIArgumentParser
    )
    admin_token_create = admin_token_subparsers.add_parser("create", help="Issue a bearer token.")
    _add_common_options(admin_token_create)
    admin_token_create.add_argument("--user", required=True, help="User/principal id.")
    admin_token_create.add_argument("--display-name", default=None, help="Optional token display name.")
    admin_token_list = admin_token_subparsers.add_parser("list", help="List tokens for a user.")
    _add_common_options(admin_token_list)
    admin_token_list.add_argument("--user", required=True, help="User/principal id.")
    admin_token_revoke = admin_token_subparsers.add_parser("revoke", help="Revoke a bearer token.")
    _add_common_options(admin_token_revoke)
    admin_token_revoke.add_argument("token_id", help="Token id, or an unambiguous token id prefix.")

    admin_project = admin_subparsers.add_parser("project", help="Manage project workspaces.")
    _add_common_options(admin_project)
    admin_project_subparsers = admin_project.add_subparsers(
        dest="admin_action", required=True, parser_class=CLIArgumentParser
    )
    admin_project_create = admin_project_subparsers.add_parser("create", help="Create a project.")
    _add_common_options(admin_project_create)
    admin_project_create.add_argument("project_id", help="Project id.")
    admin_project_create.add_argument("--owner", required=True, help="Owner user/principal id.")
    admin_project_create.add_argument("--name", default=None, help="Project display name.")
    admin_project_create.add_argument("--description", default=None, help="Optional project description.")
    admin_project_list = admin_project_subparsers.add_parser("list", help="List projects.")
    _add_common_options(admin_project_list)
    admin_project_list.add_argument("--user", default=None, help="Only list projects visible to this user.")
    admin_project_member = admin_project_subparsers.add_parser("member", help="Manage project members.")
    _add_common_options(admin_project_member)
    admin_member_subparsers = admin_project_member.add_subparsers(
        dest="admin_member_action", required=True, parser_class=CLIArgumentParser
    )
    admin_member_add = admin_member_subparsers.add_parser("add", help="Add or update a project member.")
    _add_common_options(admin_member_add)
    admin_member_add.add_argument("project_id", help="Project id.")
    admin_member_add.add_argument("--user", required=True, help="User/principal id.")
    admin_member_add.add_argument("--role", required=True, choices=["reader", "writer", "admin"])
    admin_member_list = admin_member_subparsers.add_parser("list", help="List project members.")
    _add_common_options(admin_member_list)
    admin_member_list.add_argument("project_id", help="Project id.")

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def _add_fact_filter_options(parser: argparse.ArgumentParser, *, include_predicate: bool) -> None:
    if include_predicate:
        parser.add_argument("--predicate", default=None, help="Filter by fact predicate.")
    parser.add_argument("--source", default=None, help="Filter by source conversation.")
    parser.add_argument("--date-from", default=None, help="Filter facts created from this ISO-8601 timestamp.")
    parser.add_argument("--date-to", default=None, help="Filter facts created through this ISO-8601 timestamp.")
    parser.add_argument("--confidence", default=None, help="Filter by fact confidence.")
    parser.add_argument("--status", choices=["active", "superseded", "all"], default=None, help="Filter by fact status.")
    parser.add_argument("--source-quality", default=None, help="Filter by source quality.")
    parser.add_argument("--freshness-from", default=None, help="Filter facts fresh from this ISO-8601 timestamp.")
    parser.add_argument("--freshness-to", default=None, help="Filter facts fresh through this ISO-8601 timestamp.")


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
    if args.command == "import":
        return _import_conversations(args)
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
    if args.command == "admin":
        return _admin(args)
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


def _import_conversations(args: argparse.Namespace) -> int:
    importer = get_importer(args.importer)
    text = _read_text(args.file)
    payloads = importer.import_text(text, source=args.source, title=args.title)
    if not payloads:
        raise ValueError(f"{args.importer} importer returned no conversations")

    _configure_memory_runtime(args.config)
    results = [
        redact_content_hashes(mvp_ingestion.ingest_messages(payload))
        for payload in payloads
    ]
    result = _envelope(
        status="ok",
        results=results,
        importer=args.importer,
        imported=len(results),
    )
    _emit_result(args, result, text_formatter=_format_import_text)
    return EXIT_OK


def _search(args: argparse.Namespace) -> int:
    _validate_top_k(args.top_k)
    _configure_memory_runtime(args.config)
    result = redact_content_hashes(
        mvp_ingestion.search(
            args.query,
            top_k=args.top_k,
            result_mode=args.result_mode,
            source=args.source,
            date_from=args.date_from,
            date_to=args.date_to,
            tags=args.tags,
            thread_id=args.thread_id,
        )
    )
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
            source=args.source,
            date_from=args.date_from,
            date_to=args.date_to,
            tags=args.tags,
            thread_id=args.thread_id,
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
        source=args.source,
        date_from=args.date_from,
        date_to=args.date_to,
        confidence=args.confidence,
        status=args.status,
        source_quality=args.source_quality,
        freshness_from=args.freshness_from,
        freshness_to=args.freshness_to,
    )
    _emit_result(args, redact_content_hashes(result), text_formatter=_format_fact_search_text)
    return EXIT_OK


def _profile_get(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.profile_get(
        subject=args.subject,
        predicate=args.predicate,
        source=args.source,
        date_from=args.date_from,
        date_to=args.date_to,
        confidence=args.confidence,
        status=args.status,
        source_quality=args.source_quality,
        freshness_from=args.freshness_from,
        freshness_to=args.freshness_to,
    )
    _emit_result(args, redact_content_hashes(result), text_formatter=_format_profile_text)
    return EXIT_OK


def _fact_supersede(args: argparse.Namespace) -> int:
    _configure_memory_runtime(args.config)
    result = mvp_ingestion.fact_supersede(fact_id=args.fact_id, superseded_by=args.superseded_by)
    _emit_result(args, redact_content_hashes(result), text_formatter=lambda item: item["status"])
    return EXIT_OK if result["status"] == "ok" else EXIT_COMMAND_FAILURE


def _admin(args: argparse.Namespace) -> int:
    if args.admin_resource == "user":
        if args.admin_action == "create":
            return _admin_user_create(args)
        if args.admin_action == "list":
            return _admin_user_list(args)
    if args.admin_resource == "token":
        if args.admin_action == "create":
            return _admin_token_create(args)
        if args.admin_action == "list":
            return _admin_token_list(args)
        if args.admin_action == "revoke":
            return _admin_token_revoke(args)
    if args.admin_resource == "project":
        if args.admin_action == "create":
            return _admin_project_create(args)
        if args.admin_action == "list":
            return _admin_project_list(args)
        if args.admin_action == "member":
            if args.admin_member_action == "add":
                return _admin_project_member_add(args)
            if args.admin_member_action == "list":
                return _admin_project_member_list(args)
    raise ValueError("unknown admin command")


def _admin_user_create(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    create_user = _require_store_method(store, "create_user")
    user = create_user(user_id=args.user_id, display_name=args.display_name)
    result = _envelope(status="ok", user=user)
    _emit_result(args, result, text_formatter=lambda item: _format_user_text(item["user"]))
    return EXIT_OK


def _admin_user_list(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    list_users = _require_store_method(store, "list_users")
    result = _envelope(status="ok", results=list_users())
    _emit_result(args, result, text_formatter=_format_users_text)
    return EXIT_OK


def _admin_token_create(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    create_auth_token = _require_store_method(store, "create_auth_token")
    token = _generate_bearer_token()
    token_record = create_auth_token(
        owner_id=args.user,
        token=token,
        token_display_name=args.display_name,
    )
    result = _envelope(status="ok", token=token, token_record=token_record)
    _emit_result(args, result, text_formatter=_format_token_create_text)
    return EXIT_OK


def _admin_token_list(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    list_auth_tokens = _require_store_method(store, "list_auth_tokens")
    result = _envelope(status="ok", results=list_auth_tokens(owner_id=args.user))
    _emit_result(args, result, text_formatter=_format_tokens_text)
    return EXIT_OK


def _admin_token_revoke(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    revoke_auth_token = _require_store_method(store, "revoke_auth_token")
    token_record = revoke_auth_token(args.token_id)
    if token_record is None:
        result = _envelope(
            status="not_found",
            id=args.token_id,
            error_code="not_found",
            error_message="token not found",
        )
        _emit_result(args, result, text_formatter=lambda item: item["error_message"])
        return EXIT_COMMAND_FAILURE
    result = _envelope(status="ok", token_record=token_record)
    _emit_result(args, result, text_formatter=lambda item: f"revoked {item['token_record']['token_id']}")
    return EXIT_OK


def _admin_project_create(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    create_project = _require_store_method(store, "create_project")
    project = create_project(
        project_id=args.project_id,
        owner_id=args.owner,
        name=args.name,
        description=args.description,
    )
    result = _envelope(status="ok", project=project)
    _emit_result(args, result, text_formatter=lambda item: _format_project_text(item["project"]))
    return EXIT_OK


def _admin_project_list(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    list_projects = _require_store_method(store, "list_projects")
    result = _envelope(status="ok", results=list_projects(user_id=args.user))
    _emit_result(args, result, text_formatter=_format_projects_text)
    return EXIT_OK


def _admin_project_member_add(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    add_project_member = _require_store_method(store, "add_project_member")
    add_project_member(project_id=args.project_id, user_id=args.user, role=args.role)
    result = _envelope(
        status="ok",
        member={"project_id": args.project_id, "user_id": args.user, "role": args.role},
    )
    _emit_result(args, result, text_formatter=lambda item: _format_project_member_text(item["member"]))
    return EXIT_OK


def _admin_project_member_list(args: argparse.Namespace) -> int:
    store = _admin_metadata_store(args)
    list_project_members = _require_store_method(store, "list_project_members")
    result = _envelope(status="ok", results=list_project_members(project_id=args.project_id))
    _emit_result(args, result, text_formatter=_format_project_members_text)
    return EXIT_OK


def _configure_memory_runtime(config_path: str | None) -> mvp_ingestion.RuntimeDependencies:
    config = load_config(config_path)
    return mvp_ingestion.configure_runtime(config=config)


def _admin_metadata_store(args: argparse.Namespace) -> Any:
    runtime = _configure_memory_runtime(args.config)
    return runtime.metadata_store


def _require_store_method(store: Any, name: str) -> Any:
    method = getattr(store, name, None)
    if method is None:
        raise RuntimeError(f"metadata store does not support admin method: {name}")
    return method


def _generate_bearer_token() -> str:
    return "amh_" + secrets.token_urlsafe(32)


def _read_payload(path: str) -> Any:
    text = _read_text(path)
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(text)
    return text


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _validate_top_k(value: int) -> None:
    if value < 1 or value > 100:
        raise ValueError("top_k must be an integer between 1 and 100")


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


def _format_import_text(result: dict[str, Any]) -> str:
    return f"imported {result.get('imported', 0)} conversation(s) with {result.get('importer')}"


def _format_search_text(result: dict[str, Any]) -> str:
    lines = []
    for row in result.get("results", []):
        lines.append(f"- {row.get('id')}#{row.get('chunk_index', 0)} score={row.get('score')}: {row.get('text', '')}")
        summary = _generated_summary_text(row.get("conversation"))
        if summary:
            lines.append(f"  summary: {summary}")
        auto_tags = _auto_tags_text(row.get("conversation"))
        if auto_tags:
            lines.append(f"  auto_tags: {auto_tags}")
    return "\n".join(lines) if lines else "no results"


def _format_retrieve_text(result: dict[str, Any]) -> str:
    memory = result.get("memory", {})
    messages = memory.get("messages", []) if isinstance(memory, dict) else []
    line = f"{result.get('id')} messages={len(messages)} source={memory.get('source') if isinstance(memory, dict) else ''}"
    summary = _generated_summary_text(memory)
    auto_tags = _auto_tags_text(memory)
    details = []
    if summary:
        details.append(f"summary: {summary}")
    if auto_tags:
        details.append(f"auto_tags: {auto_tags}")
    return "\n".join([line, *details]) if details else line


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
    summary = result.get("summary")
    if isinstance(summary, dict) and summary.get("text"):
        lines.append(f"summary: {summary['text']}")
    for fact in result.get("facts", []):
        if isinstance(fact, dict):
            lines.append(f"- {fact['id']} {fact['predicate']}: {fact['object']}")
    return "\n".join(lines)


def _generated_summary_text(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return None
    summary = metadata.get("generated_summary")
    if not isinstance(summary, dict):
        return None
    text = summary.get("text")
    return str(text) if text else None


def _auto_tags_text(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return None
    auto_tags = metadata.get("auto_tags")
    if not isinstance(auto_tags, list):
        return None
    tags = [str(tag) for tag in auto_tags if isinstance(tag, str)]
    return ", ".join(tags) if tags else None


def _format_user_text(user: dict[str, Any]) -> str:
    display = f" display_name={user.get('display_name')}" if user.get("display_name") else ""
    return f"user {user.get('id')}{display}"


def _format_users_text(result: dict[str, Any]) -> str:
    lines = [_format_user_text(user) for user in result.get("results", []) if isinstance(user, dict)]
    return "\n".join(lines) if lines else "no users"


def _format_token_create_text(result: dict[str, Any]) -> str:
    record = result.get("token_record", {})
    return "\n".join(
        [
            f"token_id: {record.get('token_id')}",
            f"owner_id: {record.get('owner_id')}",
            f"token: {result.get('token')}",
        ]
    )


def _format_tokens_text(result: dict[str, Any]) -> str:
    lines = []
    for token in result.get("results", []):
        if not isinstance(token, dict):
            continue
        revoked = " revoked" if token.get("revoked_at") else ""
        name = f" {token.get('display_name')}" if token.get("display_name") else ""
        prefix = f" prefix={token.get('token_prefix')}" if token.get("token_prefix") else ""
        lines.append(f"- {token.get('token_id')}{name}{prefix}{revoked}")
    return "\n".join(lines) if lines else "no tokens"


def _format_project_text(project: dict[str, Any]) -> str:
    role = f" role={project.get('role')}" if project.get("role") else ""
    return f"project {project.get('id')} owner={project.get('owner_id')} name={project.get('name')}{role}"


def _format_projects_text(result: dict[str, Any]) -> str:
    lines = [
        _format_project_text(project)
        for project in result.get("results", [])
        if isinstance(project, dict)
    ]
    return "\n".join(lines) if lines else "no projects"


def _format_project_member_text(member: dict[str, Any]) -> str:
    return f"{member.get('project_id')} {member.get('user_id')} role={member.get('role')}"


def _format_project_members_text(result: dict[str, Any]) -> str:
    lines = [
        "- " + _format_project_member_text(member)
        for member in result.get("results", [])
        if isinstance(member, dict)
    ]
    return "\n".join(lines) if lines else "no members"


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
    _redact_config_path(data, ("openai", "api_key"))
    for path in _PROVIDER_SECRET_PATHS:
        _redact_config_path(data, path)
    return data


def _redact_config_path(data: dict[str, Any], path: tuple[str, ...]) -> None:
    target: Any = data
    for key in path[:-1]:
        if not isinstance(target, dict):
            return
        target = target.get(key)
    if isinstance(target, dict) and target.get(path[-1]):
        target[path[-1]] = "***"


if __name__ == "__main__":
    raise SystemExit(main())
