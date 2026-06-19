"""Minimal ``apply_patch``-style patch parser for the eval baseline.

Format:

    *** Begin Patch
    *** Update File: path
    @@ optional context
     context
    -removed
    +added
     context
    *** End Patch

Only ``Update File`` is supported in v1. Lines beginning with a single
space are context. ``@@`` is optional; if present, its content is
ignored (it is treated as an anchor). Hunks must apply cleanly to the
current file contents. Empty lines in a hunk are accepted as blank
context lines.

A hunk may freely interleave context and ``-``/``+`` lines across
several change regions (the normal apply_patch shape) — it is matched
as a single contiguous block: the ``-``/context lines (in order) locate
the text, and the ``+``/context lines (in order) replace it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class Hunk:
    # ``old`` is the exact contiguous block to match in the file
    # (context + removed lines, in document order). ``new`` is what
    # replaces it (context + added lines, in document order). Modeling
    # the hunk as two parallel line sequences — rather than a single
    # before/removed/added/after sandwich — is what lets a hunk
    # interleave context and -/+ lines across several change regions,
    # which is the normal apply_patch shape.
    old: list[str]
    new: list[str]
    has_removed: bool


@dataclass
class PatchOp:
    action: str  # "update" only in v1
    path: str
    hunks: list[Hunk]


def parse(patch: str) -> list[PatchOp]:
    if not isinstance(patch, str):
        raise ValueError("patch must be a string")
    raw = patch.splitlines()
    if not raw or raw[0].strip() != "*** Begin Patch":
        raise ValueError("missing *** Begin Patch header")
    if raw[-1].strip() != "*** End Patch":
        raise ValueError("missing *** End Patch footer")
    body = raw[1:-1]
    ops: list[PatchOp] = []
    i = 0
    while i < len(body):
        line = body[i]
        s = line.strip()
        if s.startswith("*** Update File:"):
            path = s[len("*** Update File:"):].strip()
            i += 1
            hunks: list[Hunk] = []
            while i < len(body) and not body[i].strip().startswith("***"):
                h, end = _parse_hunk(body, i)
                hunks.append(h)
                i = end
            ops.append(PatchOp(action="update", path=path, hunks=hunks))
        elif s == "":
            i += 1
        else:
            raise ValueError(f"unexpected directive: {line!r}")
    return ops


def _parse_hunk(lines: list[str], i: int) -> tuple[Hunk, int]:
    if lines[i].strip().startswith("@@"):
        i += 1
    old: list[str] = []
    new: list[str] = []
    has_removed = False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("@@") or line.strip().startswith("***"):
            break
        if line.startswith(" "):
            content = line[1:]
            old.append(content)
            new.append(content)
        elif line == "":
            # A blank line in the body is a blank context line: it must
            # be present in the file and is preserved on both sides.
            old.append("")
            new.append("")
        elif line.startswith("-"):
            old.append(line[1:])
            has_removed = True
        elif line.startswith("+"):
            new.append(line[1:])
        else:
            raise ValueError(f"bad hunk line at {i}: {line!r}")
        i += 1
    return Hunk(old=old, new=new, has_removed=has_removed), i


def apply_to_text(op: PatchOp, text: str) -> str:
    """Apply all hunks of op to ``text``; raise on failure."""
    lines, had_nl = _split(text)
    cursor = 0
    out: list[str] = []
    for h in op.hunks:
        idx = _find_hunk(lines, cursor, h)
        if idx is None:
            raise ValueError(
                f"hunk failed at {op.path}: could not match context"
            )
        out.extend(lines[cursor:idx])
        out.extend(h.new)
        cursor = idx + len(h.old)
    out.extend(lines[cursor:])
    return _join(out, had_nl)


def _split(text: str) -> tuple[list[str], bool]:
    had = text.endswith("\n")
    body = text[:-1] if had else text
    return body.split("\n"), had


def _join(lines: list[str], had_nl: bool) -> str:
    body = "\n".join(lines)
    return body + "\n" if had_nl else body


def _find_hunk(lines: list[str], start: int, h: Hunk) -> Optional[int]:
    pat = h.old
    if not pat:
        # A pure addition with no surrounding context: append at the
        # end of the file (there is nothing to anchor against).
        return len(lines)
    for i in range(start, len(lines) - len(pat) + 1):
        if lines[i : i + len(pat)] == pat:
            return i
    return None
