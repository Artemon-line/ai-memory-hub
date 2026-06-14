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
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "fact_search",
        lambda **kwargs: {
            "status": "ok",
            "results": [
                {
                    "id": "fact-1",
                    "subject": "user",
                    "predicate": "profile_name",
                    "object": "Tyran",
                }
            ],
        },
    )

    exit_code = cli.main(["fact-search", "--subject", "user", "--json"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["id"] == "fact-1"


def test_profile_get_cli_text(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_configure_memory_runtime", lambda config_path: None)
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "profile_get",
        lambda subject: {
            "status": "ok",
            "subject": subject,
            "facts": [
                {
                    "id": "fact-1",
                    "predicate": "profile_name",
                    "object": "Tyran",
                }
            ],
        },
    )

    exit_code = cli.main(["profile-get", "--subject", "user"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "subject: user" in output
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
    monkeypatch.setattr(
        cli.mvp_ingestion,
        "search",
        lambda query, top_k, result_mode: {
            "status": "ok",
            "results": [
                {
                    "id": "memory-a",
                    "score": 0.1,
                    "chunk_index": 0,
                    "text": "hello",
                    "conversation": {"source": "codex", "timestamp": "2026-01-01T00:00:00Z", "metadata": {}},
                },
                {
                    "id": "memory-b",
                    "score": 0.2,
                    "chunk_index": 0,
                    "text": "hello",
                    "conversation": {"source": "opencode", "timestamp": "2026-01-01T00:00:00Z", "metadata": {}},
                },
            ],
        },
    )

    exit_code = cli.main(["search", "hello", "--source", "opencode", "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [row["id"] for row in body["results"]] == ["memory-b"]


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
        lambda question, top_k, max_context_tokens, result_mode: {
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
        "openai:\n  api_key: secret\nproviders:\n  metadata_dsn: postgresql://u:p@localhost/db\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["config-show", "--config", str(config_path), "--json"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["config"]["openai"]["api_key"] == "***"
    assert body["config"]["providers"]["metadata_dsn"] == "***"


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
