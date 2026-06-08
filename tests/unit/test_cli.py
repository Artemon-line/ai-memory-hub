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
