"""Local token estimation.

We use a deterministic character-based estimator that is good enough for
attribution. If ``tiktoken`` is available, prefer its cl100k_base encoder
to better match provider usage. The estimator is selected once and shared
across the run.
"""
from __future__ import annotations

from typing import Callable, Optional

try:  # pragma: no cover - optional dep
    import tiktoken  # type: ignore

    _enc = tiktoken.get_encoding("cl100k_base")

    def _tiktoken_count(text: str) -> int:
        if not text:
            return 0
        return len(_enc.encode(text))

    _impl: Callable[[str], int] = _tiktoken_count
    _IMPL_NAME = "tiktoken_cl100k_base"
except Exception:  # pragma: no cover - fallback
    def _approx_count(text: str) -> int:
        if not text:
            return 0
        # Roughly 4 characters per token; round up.
        return (len(text) + 3) // 4

    _impl = _approx_count
    _IMPL_NAME = "approx_chars_div_4"


def count_tokens(text: str) -> int:
    return _impl(text or "")


def implementation_name() -> str:
    return _IMPL_NAME


def count_messages(messages: list) -> int:
    total = 0
    for m in messages:
        if isinstance(m, dict):
            c = m.get("content") or ""
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        total += count_tokens(part.get("text", ""))
                    else:
                        total += count_tokens(str(part))
            else:
                total += count_tokens(str(c))
        else:
            total += count_tokens(str(m))
    return total
