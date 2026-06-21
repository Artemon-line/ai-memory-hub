from __future__ import annotations

import json
import tomllib

from memory import cli


def test_tokenizer_check_json_uses_config_encoding(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tokenizer:\n  encoding: test_encoding\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "tokenizer_diagnostics",
        lambda encoding: {
            "encoding": encoding,
            "available": False,
            "tokenizer_used": "heuristic",
            "cache_env": None,
            "cache_dir": None,
        },
    )

    exit_code = cli.main(["tokenizer-check", "--config", str(config_path), "--json"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["encoding"] == "test_encoding"


def test_tokenizer_check_encoding_override(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "tokenizer_diagnostics",
        lambda encoding: {
            "encoding": encoding,
            "available": True,
            "tokenizer_used": f"tiktoken:{encoding}",
            "cache_env": "TIKTOKEN_CACHE_DIR",
            "cache_dir": "D:\\tmp\\tiktoken-cache",
        },
    )

    exit_code = cli.main(["tokenizer-check", "--encoding", "override"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "encoding: override" in output
    assert "tokenizer_used: tiktoken:override" in output
    assert "cache_env: TIKTOKEN_CACHE_DIR" in output


def test_fact_search_cli_json(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    captured = {}

    def fake_fact_search(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "results": [
                {
                    "id": "fact-1",
                    "subject": "user",
                    "predicate": "profile_name",
                    "object": "Tyran",
                }
            ],
        }

    monkeypatch.setattr(cli.mvp_ingestion, "fact_search", fake_fact_search)

    exit_code = cli.main(
        [
            "fact-search",
            "--subject",
            "user",
            "--predicate",
            "profile_name",
            "--source-quality",
            "direct_user_statement",
            "--status",
            "active",
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["id"] == "fact-1"
    assert captured["predicate"] == "profile_name"
    assert captured["source_quality"] == "direct_user_statement"
    assert captured["status"] == "active"


def test_profile_get_cli_text(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    captured = {}

    def fake_profile_get(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "subject": kwargs["subject"],
            "summary": {
                "text": "profile_name: Tyran",
                "basis": "active_facts",
            },
            "facts": [
                {
                    "id": "fact-1",
                    "predicate": "profile_name",
                    "object": "Tyran",
                }
            ],
        }

    monkeypatch.setattr(cli.mvp_ingestion, "profile_get", fake_profile_get)

    exit_code = cli.main(["profile-get", "--subject", "user", "--predicate", "profile_name"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "subject: user" in output
    assert "summary: profile_name: Tyran" in output
    assert "profile_name: Tyran" in output


def test_fact_supersede_cli_returns_nonzero_when_missing(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "fact_supersede",
        lambda fact_id, superseded_by: {
            "status": "not_found",
            "id": fact_id,
            "superseded_by": superseded_by,
        },
    )

    exit_code = cli.main(["fact-supersede", "fact-old", "fact-new", "--json"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "not_found"


def test_parser_includes_core_commands() -> None:
    help_text = cli.build_parser().format_help()

    commands = (
        "ingest",
        "import",
        "search",
        "retrieve",
        "ask",
        "serve",
        "health",
        "config-show",
        "storage-check",
        "admin",
    )
    for command in commands:
        assert command in help_text


def test_pyproject_exposes_aim_console_script() -> None:
    with open("pyproject.toml", "rb") as handle:
        pyproject = tomllib.load(handle)

    assert pyproject["project"]["scripts"]["aim"] == "memory.cli:main"


def test_usage_errors_return_stable_exit_code(capsys) -> None:
    exit_code = cli.main(["search", "hello", "--top-k", "not-int"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_USAGE
    assert "usage_error" in captured.err


def test_ingest_cli_reads_json_file(capsys, monkeypatch, tmp_path) -> None:
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text('{"messages":[{"role":"user","text":"hello"}]}', encoding="utf-8")
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "ingest_messages",
        lambda payload, strict_transcript=False: {
            "status": "ok",
            "id": "memory-1",
            "chunks": 1,
        },
    )

    exit_code = cli.main(["ingest", str(payload_path), "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["id"] == "memory-1"


def test_manual_import_cli_ingests_unified_payload(capsys, monkeypatch, tmp_path) -> None:
    transcript_path = tmp_path / "copilot.txt"
    transcript_path.write_text("You: Remember the blue deployment.\nCopilot: Noted.", encoding="utf-8")
    captured_payloads = []
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "ingest_messages",
        lambda payload: captured_payloads.append(payload)
        or {"status": "ok", "id": "memory-1", "chunks": 2},
    )

    exit_code = cli.main(
        [
            "import",
            "manual",
            str(transcript_path),
            "--source",
            "vscode-copilot",
            "--title",
            "Deployment chat",
            "--json",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["imported"] == 1
    assert body["results"][0]["id"] == "memory-1"
    assert captured_payloads == [
        {
            "source": "vscode-copilot",
            "title": "Deployment chat",
            "messages": [
                {"role": "user", "text": "Remember the blue deployment."},
                {"role": "assistant", "text": "Noted."},
            ],
            "metadata": {"importer": "manual"},
        }
    ]


def test_manual_import_cli_stores_retrievable_conversation(capsys, tmp_path) -> None:
    data_dir = (tmp_path / "data").as_posix()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "providers:",
                "  embeddings: local",
                "  embedding_dimension: 32",
                "  metadata_db: sqlite",
                "  vector_db: lancedb",
                "paths:",
                f"  data_dir: {data_dir}",
            ]
        ),
        encoding="utf-8",
    )
    transcript_path = tmp_path / "manual.txt"
    transcript_path.write_text(
        "Human: Remember the amber release switch.\nClaude: I will remember it.",
        encoding="utf-8",
    )

    import_code = cli.main(
        [
            "import",
            "manual",
            str(transcript_path),
            "--config",
            str(config_path),
            "--json",
        ]
    )
    imported = json.loads(capsys.readouterr().out)
    memory_id = imported["results"][0]["id"]
    retrieve_code = cli.main(
        ["retrieve", memory_id, "--config", str(config_path), "--json"]
    )
    retrieved = json.loads(capsys.readouterr().out)

    assert import_code == 0
    assert retrieve_code == 0
    assert retrieved["memory"]["metadata"]["importer"] == "manual"
    assert retrieved["memory"]["messages"][0]["text"] == "Remember the amber release switch."


def test_search_cli_validates_top_k(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)

    exit_code = cli.main(["search", "hello", "--top-k", "0", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert body["error_code"] == "validation_error"


def test_search_cli_applies_source_filter(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    captured = {}

    def fake_search(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return {
            "status": "ok",
            "results": [
                {
                    "id": "memory-b",
                    "score": 0.2,
                    "chunk_index": 0,
                    "text": "hello",
                    "conversation": {"source": "opencode", "timestamp": "2026-01-01T00:00:00Z", "metadata": {}},
                },
            ],
        }

    monkeypatch.setattr(cli.mvp_ingestion, "search", fake_search)

    exit_code = cli.main(["search", "hello", "--source", "opencode", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [row["id"] for row in body["results"]] == ["memory-b"]
    assert captured["query"] == "hello"
    assert captured["source"] == "opencode"


def test_search_text_output_includes_generated_summary(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "search",
        lambda query, **kwargs: {
            "status": "ok",
            "results": [
                {
                    "id": "memory-b",
                    "score": 0.2,
                    "chunk_index": 0,
                    "text": "hello",
                    "conversation": {
                        "source": "opencode",
                        "metadata": {
                            "auto_tags": ["source:opencode", "gpu"],
                            "generated_summary": {
                                "text": "opencode conversation: user asked about GPU setup."
                            }
                        },
                    },
                },
            ],
        },
    )

    exit_code = cli.main(["search", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "summary: opencode conversation: user asked about GPU setup." in output
    assert "auto_tags: source:opencode, gpu" in output


def test_retrieve_text_output_includes_generated_summary(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "retrieve",
        lambda memory_id: {
            "id": memory_id,
            "source": "codex",
            "messages": [{"role": "user", "text": "hello"}],
            "metadata": {
                "auto_tags": ["source:codex", "summaries"],
                "generated_summary": {
                    "text": "codex conversation: user asked about summaries."
                }
            },
        },
    )

    exit_code = cli.main(["retrieve", "memory-b"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "summary: codex conversation: user asked about summaries." in output
    assert "auto_tags: source:codex, summaries" in output


def test_retrieve_cli_not_found_json(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(cli.mvp_ingestion, "retrieve", lambda memory_id: None)

    exit_code = cli.main(["retrieve", "missing", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert body["status"] == "not_found"
    assert body["error_code"] == "not_found"


def test_ask_cli_prints_answer_and_citations(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "ask",
        lambda question, top_k, max_context_tokens, result_mode, **kwargs: {
            "status": "ok",
            "answer": "Based on stored memory.",
            "citations": [{"id": "memory-1", "chunk_index": 0}],
            "results": [],
        },
    )

    exit_code = cli.main(["ask", "what did I store?"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Based on stored memory." in output
    assert "memory-1#0" in output


def test_config_show_redacts_secrets(capsys, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "openai:",
                "  api_key: secret",
                "providers:",
                "  metadata_dsn: postgresql://u:p@localhost/db",
                "storage:",
                "  vector_providers:",
                "    qdrant:",
                "      api_key: qdrant-secret",
                "    milvus:",
                "      token: milvus-secret",
                "    weaviate:",
                "      api_key: weaviate-secret",
                "    mongodb_atlas:",
                "      uri: mongodb+srv://user:atlas-secret@example.mongodb.net/app",
                "    elasticsearch:",
                "      username: elastic-user",
                "      password: elastic-secret",
                "    opensearch:",
                "      username: opensearch-user",
                "      password: opensearch-secret",
                "  metadata_providers:",
                "    mongodb:",
                "      uri: mongodb://user:mongo-secret@127.0.0.1:27017/app",
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(["config-show", "--config", str(config_path), "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["config"]["openai"]["api_key"] == "***"
    assert body["config"]["providers"]["metadata_dsn"] == "***"
    vector_providers = body["config"]["storage"]["vector_providers"]
    assert vector_providers["qdrant"]["api_key"] == "***"
    assert vector_providers["milvus"]["token"] == "***"
    assert vector_providers["weaviate"]["api_key"] == "***"
    assert vector_providers["mongodb_atlas"]["uri"] == "***"
    assert vector_providers["elasticsearch"]["username"] == "***"
    assert vector_providers["elasticsearch"]["password"] == "***"
    assert vector_providers["opensearch"]["username"] == "***"
    assert vector_providers["opensearch"]["password"] == "***"
    assert body["config"]["storage"]["metadata_providers"]["mongodb"]["uri"] == "***"


def test_health_cli_json(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "runtime_health",
        lambda: {"mode": "ok", "metadata_health": {"provider": "sqlite"}},
    )

    exit_code = cli.main(["health", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["health"]["mode"] == "ok"


def test_storage_check_cli_json(capsys, monkeypatch) -> None:
    class Store:
        def health(self):
            return {"provider": "memory"}

        def capabilities(self):
            return type("Caps", (), {"supports_transactions": True})()

    runtime = type("Runtime", (), {"metadata_store": Store(), "vector_store": Store()})()
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: runtime)

    exit_code = cli.main(["storage-check", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["metadata"]["provider"] == "memory"


def test_admin_token_create_json_prints_raw_token_once(capsys, monkeypatch) -> None:
    class Store:
        def create_auth_token(self, **kwargs):
            assert kwargs["owner_id"] == "jane"
            assert kwargs["token"] == "amh_raw_once"
            assert kwargs["token_display_name"] == "laptop"
            return {
                "token_id": "tok_123",
                "owner_id": "jane",
                "display_name": "laptop",
                "token_prefix": "amh_raw_once",
                "created_at": "2026-06-17T00:00:00Z",
                "expires_at": None,
                "revoked_at": None,
            }

    runtime = type("Runtime", (), {"metadata_store": Store()})()
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: runtime)
    monkeypatch.setattr(cli, "_generate_bearer_token", lambda: "amh_raw_once")

    exit_code = cli.main(
        [
            "admin",
            "token",
            "create",
            "--user",
            "jane",
            "--display-name",
            "laptop",
            "--json",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["token"] == "amh_raw_once"
    assert body["token_record"]["token_id"] == "tok_123"
    assert "token_hash" not in json.dumps(body)


def test_admin_token_list_does_not_expose_raw_tokens(capsys, monkeypatch) -> None:
    class Store:
        def list_auth_tokens(self, *, owner_id):
            assert owner_id == "jane"
            return [
                {
                    "token_id": "tok_123",
                    "owner_id": "jane",
                    "display_name": "laptop",
                    "token_prefix": "amh_abcd1234",
                    "created_at": "2026-06-17T00:00:00Z",
                    "expires_at": None,
                    "revoked_at": None,
                }
            ]

    runtime = type("Runtime", (), {"metadata_store": Store()})()
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: runtime)

    exit_code = cli.main(["admin", "token", "list", "--user", "jane", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["results"][0]["token_id"] == "tok_123"
    assert "amh_raw_once" not in json.dumps(body)
    assert "token_hash" not in json.dumps(body)


def test_admin_user_project_and_member_cli_json(capsys, monkeypatch) -> None:
    class Store:
        def __init__(self):
            self.members = []

        def create_user(self, *, user_id, display_name):
            return {
                "id": user_id,
                "display_name": display_name,
                "created_at": "2026-06-17T00:00:00Z",
                "disabled_at": None,
            }

        def create_project(self, *, project_id, owner_id, name, description):
            return {
                "id": project_id,
                "owner_id": owner_id,
                "name": name,
                "description": description,
                "is_default": False,
                "created_at": "2026-06-17T00:00:00Z",
                "updated_at": "2026-06-17T00:00:00Z",
                "archived_at": None,
                "role": None,
            }

        def add_project_member(self, *, project_id, user_id, role):
            self.members.append({"project_id": project_id, "user_id": user_id, "role": role})

        def list_project_members(self, *, project_id):
            return [member for member in self.members if member["project_id"] == project_id]

    store = Store()
    runtime = type("Runtime", (), {"metadata_store": store})()
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: runtime)

    user_code = cli.main(
        ["admin", "user", "create", "jane", "--display-name", "Jane", "--json"]
    )
    user = json.loads(capsys.readouterr().out)
    project_code = cli.main(
        [
            "admin",
            "project",
            "create",
            "shared-321",
            "--owner",
            "jane",
            "--name",
            "Shared",
            "--json",
        ]
    )
    project = json.loads(capsys.readouterr().out)
    member_add_code = cli.main(
        [
            "admin",
            "project",
            "member",
            "add",
            "shared-321",
            "--user",
            "carl",
            "--role",
            "writer",
            "--json",
        ]
    )
    member_add = json.loads(capsys.readouterr().out)
    member_list_code = cli.main(
        ["admin", "project", "member", "list", "shared-321", "--json"]
    )
    member_list = json.loads(capsys.readouterr().out)

    assert user_code == 0
    assert project_code == 0
    assert member_add_code == 0
    assert member_list_code == 0
    assert user["user"]["id"] == "jane"
    assert project["project"]["id"] == "shared-321"
    assert member_add["member"]["role"] == "writer"
    assert member_list["results"] == [{"project_id": "shared-321", "user_id": "carl", "role": "writer"}]


def test_admin_token_revoke_not_found(capsys, monkeypatch) -> None:
    class Store:
        def revoke_auth_token(self, token_id):
            assert token_id == "tok_missing"
            return None

    runtime = type("Runtime", (), {"metadata_store": Store()})()
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: runtime)

    exit_code = cli.main(["admin", "token", "revoke", "tok_missing", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert body["status"] == "not_found"


def test_serve_cli_uses_config_host_port(capsys, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api:\n  host: 127.0.0.2\n  port: 9999\n", encoding="utf-8")
    calls = {}
    monkeypatch.setattr(cli, "_create_cli_app", lambda config: "app")
    monkeypatch.setattr(
        cli,
        "_run_server",
        lambda app, host, port: calls.update({"app": app, "host": host, "port": port}),
    )

    exit_code = cli.main(["serve", "--config", str(config_path)])

    assert exit_code == 0
    assert calls == {"app": "app", "host": "127.0.0.2", "port": 9999}
    assert "serving ai-memory-hub" in capsys.readouterr().out


def test_cli_ingest_search_retrieve_ask_with_temporary_storage(capsys, tmp_path) -> None:
    data_dir = (tmp_path / "data").as_posix()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "providers:",
                "  embeddings: local",
                "  embedding_dimension: 32",
                "  metadata_db: sqlite",
                "  vector_db: lancedb",
                "paths:",
                f"  data_dir: {data_dir}",
            ]
        ),
        encoding="utf-8",
    )
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text(
        json.dumps(
            {
                "source": "cli-test",
                "timestamp": "2026-01-01T00:00:00Z",
                "messages": [{"role": "user", "text": "remember the cli gpu plan"}],
            }
        ),
        encoding="utf-8",
    )

    ingest_code = cli.main(["ingest", str(payload_path), "--config", str(config_path), "--json"])
    ingest = json.loads(capsys.readouterr().out)
    search_code = cli.main(["search", "cli gpu plan", "--config", str(config_path), "--json"])
    search = json.loads(capsys.readouterr().out)
    retrieve_code = cli.main(["retrieve", ingest["id"], "--config", str(config_path), "--json"])
    retrieve = json.loads(capsys.readouterr().out)
    ask_code = cli.main(["ask", "cli gpu plan", "--config", str(config_path), "--json"])
    ask = json.loads(capsys.readouterr().out)

    assert ingest_code == 0
    assert search_code == 0
    assert retrieve_code == 0
    assert ask_code == 0
    assert ingest["status"] == "ok"
    assert search["results"]
    assert retrieve["memory"]["id"] == ingest["id"]
    assert ask["citations"]
