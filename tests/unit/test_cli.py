from __future__ import annotations

import json

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

    exit_code = cli.main(["fact-supersede", "fact-old", "fact-new"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "not_found"
