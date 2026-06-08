from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_FALLBACK_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_warned_fallback = False


def count_tokens(text: str, encoding: str) -> int:
    tokenizer = _get_encoding(encoding)
    if tokenizer is not None:
        return len(tokenizer.encode(text))
    return len(_fallback_tokens(text))


def truncate_to_tokens(text: str, max_tokens: int, encoding: str) -> str:
    if max_tokens <= 0:
        return ""

    tokenizer = _get_encoding(encoding)
    if tokenizer is not None:
        token_ids = tokenizer.encode(text)
        if len(token_ids) <= max_tokens:
            return text
        return tokenizer.decode(token_ids[:max_tokens]).rstrip()

    tokens = _fallback_tokens(text)
    if len(tokens) <= max_tokens:
        return text
    return "".join(tokens[:max_tokens]).rstrip()


def split_token_windows(
    text: str, *, max_tokens: int, overlap_tokens: int, encoding: str
) -> list[str]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must be non-negative")
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be less than max_tokens")

    tokenizer = _get_encoding(encoding)
    if tokenizer is not None:
        token_ids = tokenizer.encode(text)
        if len(token_ids) <= max_tokens:
            return [text]
        windows: list[str] = []
        step = max_tokens - overlap_tokens
        for start in range(0, len(token_ids), step):
            window = tokenizer.decode(token_ids[start : start + max_tokens]).strip()
            if window:
                windows.append(window)
            if start + max_tokens >= len(token_ids):
                break
        return windows

    tokens = re.findall(r"\S+", text)
    if len(tokens) <= max_tokens:
        return [text]
    windows = []
    step = max_tokens - overlap_tokens
    for start in range(0, len(tokens), step):
        window = " ".join(tokens[start : start + max_tokens]).strip()
        if window:
            windows.append(window)
        if start + max_tokens >= len(tokens):
            break
    return windows


def tokenizer_used(encoding: str) -> str:
    if _get_encoding(encoding) is not None:
        return f"tiktoken:{encoding}"
    return "heuristic"


def tokenizer_diagnostics(encoding: str) -> dict[str, Any]:
    tokenizer = _get_encoding(encoding)
    cache_env = None
    cache_dir = None
    if "TIKTOKEN_CACHE_DIR" in os.environ:
        cache_env = "TIKTOKEN_CACHE_DIR"
        cache_dir = os.environ["TIKTOKEN_CACHE_DIR"]
    elif "DATA_GYM_CACHE_DIR" in os.environ:
        cache_env = "DATA_GYM_CACHE_DIR"
        cache_dir = os.environ["DATA_GYM_CACHE_DIR"]

    return {
        "encoding": encoding,
        "available": tokenizer is not None,
        "tokenizer_used": f"tiktoken:{encoding}" if tokenizer is not None else "heuristic",
        "cache_env": cache_env,
        "cache_dir": cache_dir,
    }


@lru_cache(maxsize=8)
def _get_encoding(encoding: str) -> Any | None:
    global _warned_fallback
    try:
        import tiktoken

        return tiktoken.get_encoding(encoding)
    except Exception as exc:
        if not _warned_fallback:
            logger.warning(
                "Token counting is using heuristic fallback because tiktoken is unavailable or encoding failed: %s",
                exc,
            )
            _warned_fallback = True
        return None


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    cursor = 0
    for match in _FALLBACK_PATTERN.finditer(text):
        if match.start() > cursor:
            tokens.append(text[cursor : match.start()])
        tokens.append(match.group(0))
        cursor = match.end()
    if cursor < len(text):
        tokens.append(text[cursor:])
    return [token for token in tokens if token]
