"""Per-run state: span registry, revision tracking, and allowed paths."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .util import file_rev, lines_with_endings, safe_resolve


@dataclass
class Span:
    id: str
    path: str  # repo-relative
    rev: str
    start: int  # 1-based
    count: int
    hash: str  # 8-char span hash

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "rev": self.rev,
            "range": f"{self.start}+{self.count}",
            "hash": self.hash,
        }


class ToolState:
    """Holds the per-run tool state.

    The worktree is a real directory the tools mutate. Spans and
    revisions are tracked here so that mutations can fail-fast on stale
    handles.
    """

    RG_MAX_MATCHES = 80
    READ_RANGE_DEFAULT_MAX = 120
    DIFF_HUNK_MAX_LINES = 200
    RUN_CHECK_MAX_LINES = 120

    def __init__(
        self,
        worktree: Path,
        allowed_paths: list[str],
        checks: list[dict],
    ) -> None:
        self.worktree = worktree.resolve()
        self.allowed_paths = [self._norm(p) for p in allowed_paths]
        self.checks = checks
        self._spans: dict[str, Span] = {}
        self._span_counter = 0
        self._diff_counter = 0
        self._output_counter = 0

    # -- public helpers --------------------------------------------------
    def is_path_allowed(self, rel: str) -> bool:
        rel = self._norm(rel)
        if not self.allowed_paths:
            return True
        for ap in self.allowed_paths:
            if rel == ap or rel.startswith(ap.rstrip("/") + "/"):
                return True
        return False

    def resolve(self, rel: str) -> Optional[Path]:
        rel = rel.lstrip("/")
        return safe_resolve(self.worktree, rel)

    def current_rev(self, rel: str) -> str:
        p = self.resolve(rel)
        if p is None:
            return "r" + "0" * 7
        return file_rev(p)

    def span_lines(self, span: Span) -> list[str]:
        p = self.resolve(span.path)
        if p is None:
            return []
        lines, _ = lines_with_endings(p)
        return lines[span.start - 1 : span.start - 1 + span.count]

    def new_span(self, path: str, start: int, count: int) -> Span:
        self._span_counter += 1
        sid = f"S{self._span_counter}"
        p = self.resolve(path)
        if p is None:
            raise ValueError(f"cannot resolve path {path!r}")
        lines, _ = lines_with_endings(p)
        from .util import line_hash
        h = line_hash(lines, start, count)
        rev = file_rev(p)
        sp = Span(id=sid, path=path, rev=rev, start=start, count=count, hash=h)
        self._spans[sid] = sp
        return sp

    def get_span(self, sid: str) -> Optional[Span]:
        return self._spans.get(sid)

    def drop_spans_for(self, path: str) -> None:
        path = self._norm(path)
        for sid in list(self._spans):
            if self._spans[sid].path == path:
                self._spans.pop(sid, None)

    def new_diff_handle(self) -> str:
        self._diff_counter += 1
        return f"D{self._diff_counter}"

    def new_output_handle(self) -> str:
        self._output_counter += 1
        return f"O{self._output_counter}"

    # -- internal --------------------------------------------------------
    @staticmethod
    def _norm(rel: str) -> str:
        rel = rel.replace(os.sep, "/")
        # Strip a leading "./" prefix (repeatedly), then any leading
        # slashes. Use prefix removal, not lstrip(".../"), which is a
        # character set and would mangle paths like ".gitignore".
        while rel.startswith("./"):
            rel = rel[2:]
        while rel.startswith("/"):
            rel = rel[1:]
        return rel
