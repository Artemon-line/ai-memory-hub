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
