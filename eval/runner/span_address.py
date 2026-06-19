"""Address syntax for span edit ops.

Supports two address forms:

Path-based (file-line coordinates). Two flavors:
  - path:line             resolves against the current rev on disk
  - path:line+count       line range, current rev
  - path@rev:line         pinned to a specific rev
  - path@rev:line+count   pinned rev, line range
  - path@rev:EOF          pinned rev, end of file

Span-based (handles returned by read_range/rg):
  - S17                   whole span
  - S17:rel_line          1-based line offset within the span
  - S17:rel_start+count   line range within the span
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_PATH_REV_ADDR = re.compile(
    r"^(?P<path>[^@]+)@(?P<rev>r[0-9a-f]{7}):(?P<rest>.+)$"
)
# Path-only (no @rev): resolves against the current rev of the file.
# Rejects bare paths with no line and EOF (use path@rev:EOF for that).
_PATH_ONLY_ADDR = re.compile(
    r"^(?P<path>[^@:]+):(?P<line>\d+)(?:\+(?P<count>\d+))?$"
)
_SPAN_ADDR = re.compile(r"^S(?P<id>\d+)(?::(?P<rest>.+))?$")


@dataclass
class Address:
    """Resolved address pointing to a region in a file.

    ``start`` and ``count`` are 1-based. ``count == 0`` means an insertion
    point before ``start``.
    """
    path: str
    rev: str
    start: int
    count: int
    is_eof: bool = False
    span_id: Optional[str] = None  # for diagnostics

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "rev": self.rev,
            "range": ("EOF" if self.is_eof else f"{self.start}+{self.count}"),
        }


def parse_address(spec: str) -> tuple[Optional[str], Optional[str], Optional[int], Optional[int], bool]:
    """Parse an address spec. Returns (path, rev, start, count, is_eof).

    Returns all-None on parse failure. The caller is expected to also
    accept bare span ids (which need a state lookup).

    If the spec is a path-only form (no @rev), the returned rev is None
    and the caller is expected to resolve it via state.file_rev(path).
    """
    if not isinstance(spec, str) or not spec:
        return None, None, None, None, False
    s = spec.strip()
    m = _PATH_REV_ADDR.match(s)
    if m:
        path = m.group("path")
        rev = m.group("rev")
        rest = m.group("rest")
        if rest == "EOF":
            return path, rev, None, None, True
        if "+" in rest:
            a, b = rest.split("+", 1)
            try:
                start = int(a)
                count = int(b)
            except ValueError:
                return None, None, None, None, False
            return path, rev, start, count, False
        try:
            start = int(rest)
        except ValueError:
            return None, None, None, None, False
        # Bare line (no +count): treat as a point address (count=0).
        # Callers that want a span must write `path:N+M`.
        return path, rev, start, 0, False
    m = _PATH_ONLY_ADDR.match(s)
    if m:
        path = m.group("path")
        start = int(m.group("line"))
        count = int(m.group("count")) if m.group("count") else 0
        # rev=None signals "current rev" to the caller.
        return path, None, start, count, False
    return None, None, None, None, False


def parse_span_ref(spec: str) -> Optional[tuple[str, Optional[int], Optional[int]]]:
    """Return (span_id, start, count) for a span-based address, or None."""
    if not isinstance(spec, str) or not spec:
        return None
    s = spec.strip()
    m = _SPAN_ADDR.match(s)
    if not m:
        return None
    sid = f"S{m.group('id')}"
    rest = m.group("rest")
    if rest is None:
        return sid, None, None
    if "+" in rest:
        a, b = rest.split("+", 1)
        try:
            start = int(a)
            count = int(b)
        except ValueError:
            return None
        return sid, start, count
    try:
        start = int(rest)
    except ValueError:
        return None
    return sid, start, 1


def resolve_address(spec: str, state, default_path: Optional[str] = None) -> Optional[Address]:
    """Resolve any address form against ``state``.

    Returns None on failure (caller turns that into a tool error).
    """
    s = spec.strip()
    # Span form first
    sr = parse_span_ref(s)
    if sr:
        sid, rel_start, rel_count = sr
        span = state.get_span(sid)
        if span is None:
            return None
        if rel_start is None:
            return Address(
                path=span.path,
                rev=span.rev,
                start=span.start,
                count=span.count,
                span_id=sid,
            )
        # 1-based relative-to-span. Clamp to span bounds.
        count = rel_count if rel_count is not None else 1
        if rel_start < 1 or count < 0:
            return None
        abs_start = span.start + (rel_start - 1)
        span_last = span.start + span.count - 1
        if count == 0:
            # Insertion point: allow before any line in the span, or one
            # past the span to append after the returned range.
            if abs_start < span.start or abs_start > span_last + 1:
                return None
        else:
            # Replacement/deletion range must be fully inside the span.
            if abs_start < span.start or abs_start > span_last:
                return None
            if abs_start + count - 1 > span_last:
                return None
        return Address(
            path=span.path,
            rev=span.rev,
            start=abs_start,
            count=count,
            span_id=sid,
        )
    # Path form
    path, rev, start, count, is_eof = parse_address(s)
    if path is None:
        return None
    if default_path and path == "":
        path = default_path
    if rev is None:
        # Path-only form: pin to the current rev of the file on disk.
        try:
            rev = state.current_rev(path)
        except Exception:
            return None
    return Address(
        path=path,
        rev=rev,
        start=start if start is not None else 0,
        count=count if count is not None else 0,
        is_eof=is_eof,
    )
