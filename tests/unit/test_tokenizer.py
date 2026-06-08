from __future__ import annotations

from memory.ingestion import tokenizer


def test_tokenizer_fallback_counts_and_truncates(monkeypatch) -> None:
    monkeypatch.setattr(tokenizer, "_get_encoding", lambda encoding: None)

    text = "alpha beta gamma"

    assert tokenizer.count_tokens(text, "missing") >= 3
    truncated = tokenizer.truncate_to_tokens(text, 3, "missing")
    assert tokenizer.count_tokens(truncated, "missing") <= 3
    assert tokenizer.tokenizer_used("missing") == "heuristic"


def test_tokenizer_fallback_splits_overlapping_windows(monkeypatch) -> None:
    monkeypatch.setattr(tokenizer, "_get_encoding", lambda encoding: None)

    windows = tokenizer.split_token_windows(
        "alpha beta gamma delta epsilon",
        max_tokens=3,
        overlap_tokens=1,
        encoding="missing",
    )

    assert windows == ["alpha beta gamma", "gamma delta epsilon"]


def test_tokenizer_diagnostics_reports_tiktoken(monkeypatch) -> None:
    monkeypatch.setattr(tokenizer, "_get_encoding", lambda encoding: object())

    diagnostics = tokenizer.tokenizer_diagnostics("cl100k_base")

    assert diagnostics["encoding"] == "cl100k_base"
    assert diagnostics["available"] is True
    assert diagnostics["tokenizer_used"] == "tiktoken:cl100k_base"


def test_tokenizer_diagnostics_reports_heuristic_and_cache(monkeypatch) -> None:
    monkeypatch.setattr(tokenizer, "_get_encoding", lambda encoding: None)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "D:\\tmp\\tiktoken-cache")

    diagnostics = tokenizer.tokenizer_diagnostics("missing")

    assert diagnostics["encoding"] == "missing"
    assert diagnostics["available"] is False
    assert diagnostics["tokenizer_used"] == "heuristic"
    assert diagnostics["cache_env"] == "TIKTOKEN_CACHE_DIR"
    assert diagnostics["cache_dir"] == "D:\\tmp\\tiktoken-cache"
