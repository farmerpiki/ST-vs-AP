"""The ``edit_batch`` write tool for the span_tools variant."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .span_address import Address, parse_address, resolve_address
from .state import ToolState
from .util import file_rev, lines_with_endings, unified_diff_hunks, write_lines


# ---------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------


@dataclass
class FilePlan:
    """Per-file edit plan in original-snapshot coordinates."""
    path: str
    rev: str
    lines: list[str]
    had_nl: bool
    # Each entry is a (start, count, new_lines) splice to apply, in
    # original-snapshot coordinates. Multiple entries at the same
    # insertion point (start, count=0) are allowed and applied in given
    # order.
    splices: list[tuple[int, int, list[str]]] = field(default_factory=list)


# ---------------------------------------------------------------------
# Tool entry
# ---------------------------------------------------------------------


def tool_edit_batch(args: dict, state: ToolState) -> dict:
    ops = args.get("ops")
    if not isinstance(ops, list) or not ops:
        return {"ok": False, "error": "missing_ops"}
    if not args.get("atomic", True):
        return {"ok": False, "error": "atomic_required"}
    if args.get("coords", "snapshot") != "snapshot":
        return {"ok": False, "error": "snapshot_required"}
    allowed_root = {"atomic", "coords", "ops", "verify"}
    if any(k not in allowed_root for k in args):
        return {
            "ok": False,
            "error": "op_parse_error",
            "details": "edit_batch: unsupported field; allowed keys: atomic, coords, ops, verify",
        }

    # 1. parse
    try:
        parsed = [_parse_op(o) for o in ops]
    except _OpError as e:
        return {"ok": False, "error": "op_parse_error", "details": str(e)}

    # 2-4. resolve addresses, check allowed paths, expand replace_word
    try:
        _resolve_and_expand(parsed, state)
    except _OpError as e:
        return {"ok": False, "error": "resolve_error", "details": str(e)}

    # 5+6. expected counts
    for p in parsed:
        if p.get("kind") == "replace_word":
            total = p.get("_count", 0)
            exp = p.get("expected") or {}
            if not p.get("dry_run"):
                if "exact" in exp and exp["exact"] != total:
                    return {
                        "ok": False,
                        "error": "expected_count_mismatch",
                        "from": p.get("from"),
                        "expected": exp.get("exact"),
                        "actual": total,
                    }
                if "min" in exp and total < exp["min"]:
                    return {
                        "ok": False,
                        "error": "expected_count_below_min",
                        "expected_min": exp["min"],
                        "actual": total,
                    }
            else:
                # Dry run: report counts and stop here.
                per_path_counts: list[dict] = []
                for path, n in p.get("_per_path", []):
                    per_path_counts.append({"path": path, "count": n})
                return {
                    "ok": True,
                    "dry_run": True,
                    "matches": per_path_counts,
                    "total": total,
                }

    # 7. overlap detection
    try:
        _check_overlaps(parsed)
    except _OpError as e:
        return {"ok": False, "error": "overlap_error", "details": str(e)}

    # 8. move destination check (same-file dest inside source)
    try:
        _check_move_destinations(parsed)
    except _OpError as e:
        return {"ok": False, "error": "move_destination_error", "details": str(e)}

    # 9-11. apply
    try:
        result = _apply_all(parsed, state)
    except _OpError as e:
        return {"ok": False, "error": "apply_error", "details": str(e)}
    # verify=true runs the build right after a SUCCESSFUL edit (this turn),
    # collapsing edit+build. Only when something actually changed.
    if args.get("verify") and result.get("ok") and result.get("changed"):
        from .tools_shared import run_default_check
        vr = run_default_check(state)
        if vr is not None:
            result["verify"] = vr
    return result


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------


class _OpError(Exception):
    pass


def _address_hint(spec: str) -> str:
    """Best-effort hint for an unparseable address."""
    s = spec.strip()
    if not s:
        return "empty address"
    if s.startswith("S") and not s[1:2].isdigit():
        return "expected span id like 'S17' (digits after S)"
    if s.startswith("S") and ":" in s:
        head, tail = s[1:].split(":", 1)
        if not head.isdigit():
            return f"bad span id {head!r}"
        if tail and not ("+" in tail or tail.isdigit() or tail == "EOF"):
            return f"bad span-relative spec {tail!r}; use N or N+M or EOF"
    if "@" in s:
        path, _, rest = s.partition("@")
        if not rest.startswith("r"):
            return f"expected rev (e.g. 'r8f31a2') after @, got {rest!r}"
        rev = rest[1:].split(":", 1)[0]
        if len(rev) != 7 or not all(c in "0123456789abcdef" for c in rev):
            return f"bad rev {rev!r}; expected 7 hex chars like 'r8f31a2'"
        # The rest after the rev
        after = rest[len("r" + rev):]
        if not after.startswith(":"):
            return f"expected ':' after rev in {s!r}"
        tail = after[1:]
        if tail and not (tail == "EOF" or "+" in tail or tail.isdigit()):
            return f"bad address tail {tail!r}; use N or N+M or EOF"
    return ("use one of: 'path:line', 'path:line+count', "
            "'path@rev:line', 'path@rev:line+count', 'path@rev:EOF', "
            "'S17', 'S17:line', 'S17:line+count'")


def _reject_extra_fields(o: dict, op_name: str, allowed: set[str]) -> None:
    if any(k not in allowed for k in o):
        keys = ", ".join(sorted(allowed))
        raise _OpError(f"{op_name}: unsupported field; allowed keys: {keys}")


def _parse_op(o: dict) -> dict:
    if not isinstance(o, dict):
        raise _OpError("op must be an object")
    op = o.get("op")
    if op == "splice":
        _reject_extra_fields(o, "splice", {"op", "at", "text"})
        at = o.get("at")
        text = o.get("text")
        if not isinstance(at, str):
            raise _OpError("splice: at required")
        if text is not None and not isinstance(text, str):
            raise _OpError("splice: text must be string if given")
        return {"kind": "splice", "at": at, "text": text}
    if op == "move_span":
        _reject_extra_fields(o, "move_span", {"op", "from", "to", "moved_indent_delta"})
        # Destination address is exact:
        #   to: path:N      -> insert at line N (existing line N is pushed down)
        #   to: path:N+M    -> replace the M-line destination range with the moved body.
        # The source span is always removed. Use splice for any separate insertion.
        frm = o.get("from")
        to = o.get("to")
        if not isinstance(frm, str) or not isinstance(to, str):
            raise _OpError("move_span: from and to required")
        return {
            "kind": "move_span",
            "from": frm,
            "to": to,
            "moved_indent_delta": int(o.get("moved_indent_delta", 0) or 0),
        }
    if op == "replace_word":
        _reject_extra_fields(o, "replace_word", {"op", "paths", "from", "to", "boundary", "include", "expected", "dry_run"})
        paths = o.get("paths")
        frm = o.get("from")
        to = o.get("to")
        if not isinstance(paths, list) or not isinstance(frm, str) or not isinstance(to, str):
            raise _OpError("replace_word: paths, from, to required")
        boundary = o.get("boundary", "cpp_identifier")
        if boundary != "cpp_identifier":
            raise _OpError("replace_word: only cpp_identifier boundary supported")
        include = o.get("include", "code_and_comments")
        if include not in ("all_text", "code_and_comments", "code_only"):
            raise _OpError("replace_word: invalid include")
        return {
            "kind": "replace_word",
            "paths": paths,
            "from": frm,
            "to": to,
            "include": include,
            "expected": o.get("expected"),
            "dry_run": bool(o.get("dry_run", False)),
        }
    raise _OpError("unknown op; allowed ops: splice, move_span, replace_word")


# ---------------------------------------------------------------------
# Resolve and expand
# ---------------------------------------------------------------------


def _resolve_and_expand(parsed: list[dict], state: ToolState) -> None:
    for p in parsed:
        kind = p["kind"]
        if kind == "splice":
            addr = resolve_address(p["at"], state)
            if addr is None:
                hint = _address_hint(p['at'])
                raise _OpError(f"splice: cannot resolve address {p['at']!r}. {hint}")
            if not state.is_path_allowed(addr.path):
                raise _OpError(f"splice: path not allowed {addr.path!r}")
            _check_rev(addr, state)
            text_arg = p.get("text")
            if addr.count == 0:
                if text_arg is None or text_arg == "":
                    raise _OpError("splice: +0 is insert-only and requires non-empty text; use +N with empty text to delete")
                raw_at = str(p.get("at", ""))
                if "+0" not in raw_at and not raw_at.endswith(":EOF"):
                    raise _OpError("splice insert must spell the point explicitly as +0 (e.g. path:N+0 or S#:N+0); line replace uses +1")
            p["_addr"] = addr
        elif kind == "move_span":
            frm = resolve_address(p["from"], state)
            to = resolve_address(p["to"], state)
            if frm is None:
                raise _OpError(f"move_span: cannot resolve from {p['from']!r}")
            if to is None:
                raise _OpError(f"move_span: cannot resolve to {p['to']!r}")
            if not state.is_path_allowed(frm.path) or not state.is_path_allowed(to.path):
                raise _OpError("move_span: path not allowed")
            _check_rev(frm, state)
            _check_rev(to, state)
            p["_from"] = frm
            p["_to"] = to
        elif kind == "replace_word":
            for rel in p["paths"]:
                if not state.is_path_allowed(rel):
                    raise _OpError(f"replace_word: path not allowed {rel!r}")
            total = 0
            per_path: list[tuple[str, int]] = []
            edits: dict[str, list[tuple[int, int, list[str]]]] = {}
            for rel in p["paths"]:
                p_abs = state.resolve(rel)
                if p_abs is None or not p_abs.is_file():
                    raise _OpError(f"replace_word: file not found {rel!r}")
                lines, _ = lines_with_endings(p_abs)
                per_line: dict[int, str] = {}
                for i, line in enumerate(lines, start=1):
                    if _has_word(line, p["from"]):
                        per_line[i] = _replace_word_in_line(line, p["from"], p["to"])
                n = sum(_count_word(line, p["from"]) for line in lines)
                total += n
                per_path.append((rel, n))
                edits[rel] = [(ln, 1, [new]) for ln, new in sorted(per_line.items())]
            p["_edits"] = edits
            p["_count"] = total
            p["_per_path"] = per_path


def _check_rev(addr: Address, state: ToolState) -> None:
    actual = state.current_rev(addr.path)
    if actual != addr.rev:
        raise _OpError(
            f"stale_rev: expected {addr.rev}, actual {actual} at {addr.path}"
        )


def _iter_word_matches(line: str, word: str):
    pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])")
    return pat.finditer(line)


def _count_word(line: str, word: str) -> int:
    return sum(1 for _ in _iter_word_matches(line, word))


def _has_word(line: str, word: str) -> bool:
    return any(True for _ in _iter_word_matches(line, word))


def _replace_word_in_line(line: str, frm: str, to: str) -> str:
    return re.sub(
        rf"(?<![A-Za-z0-9_]){re.escape(frm)}(?![A-Za-z0-9_])",
        to,
        line,
    )


# ---------------------------------------------------------------------
# Overlap + move destination checks
# ---------------------------------------------------------------------


def _check_overlaps(parsed: list[dict]) -> None:
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for p in parsed:
        if p["kind"] == "splice":
            a = p["_addr"]
            edits.setdefault(a.path, []).append((a.start, a.count, "splice"))
        elif p["kind"] == "move_span":
            a = p["_from"]
            edits.setdefault(a.path, []).append((a.start, a.count, "move_source"))
        elif p["kind"] == "replace_word":
            for path, lst in p.get("_edits", {}).items():
                for (s, c, _) in lst:
                    edits.setdefault(path, []).append((s, c, "replace_word"))

    for path, lst in edits.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        for i in range(len(lst)):
            s1, c1, k1 = lst[i]
            e1 = s1 + max(c1, 1) - 1
            for j in range(i + 1, len(lst)):
                s2, c2, k2 = lst[j]
                if s2 > e1:
                    break
                e2 = s2 + max(c2, 1) - 1
                # Two zero-count inserts at the same line are allowed.
                if c1 == 0 and c2 == 0 and s1 == s2:
                    continue
                # replace_word + explicit splice overlap: forbidden.
                if {k1, k2} == {"replace_word", "splice"} or {
                    k1, k2
                } == {"replace_word", "splice"}:
                    raise _OpError(
                        f"replace_word touches a line also edited by "
                        f"{k1 if k1 != 'replace_word' else k2} on {path}"
                    )
                # Two move sources overlap: forbidden.
                if k1 == "move_source" and k2 == "move_source":
                    raise _OpError(
                        f"two move_span sources overlap on {path}"
                    )
                # move_source overlaps a splice: forbidden.
                if "move_source" in (k1, k2):
                    raise _OpError(
                        f"move_span source overlaps {k1 if k1 != 'move_source' else k2} on {path}"
                    )
                raise _OpError(
                    f"overlapping edits on {path} at line {s2}"
                )


def _check_move_destinations(parsed: list[dict]) -> None:
    # The destination address is exact. We do allow the destination
    # to be inside the source range: the source is consumed first
    # (the lines are removed) and the body lands at the (now-valid)
    # destination position. This matches the intuitive model of
    # "move this block to there".
    return


# ---------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------


def _apply_all(parsed: list[dict], state: ToolState) -> dict:
    plans: dict[str, FilePlan] = {}

    def _ensure(path: str, rev: str) -> FilePlan:
        if path in plans:
            return plans[path]
        p_abs = state.resolve(path)
        lines, had = lines_with_endings(p_abs)
        plans[path] = FilePlan(
            path=path, rev=rev or state.current_rev(path), lines=lines, had_nl=had
        )
        return plans[path]

    # 1. splice + replace_word become (start, count, new_lines).
    for p in parsed:
        if p["kind"] == "splice":
            a = p["_addr"]
            pl = _ensure(a.path, a.rev)
            if a.count == 0:
                new_lines = _split_text_lines(p.get("text") or "")
            else:
                new_lines = _split_text_lines(p.get("text") or "")
            pl.splices.append((a.start, a.count, new_lines))
        elif p["kind"] == "replace_word":
            for rel, lst in p.get("_edits", {}).items():
                pl = _ensure(rel, state.current_rev(rel))
                for (s, c, new) in lst:
                    pl.splices.append((s, c, new))

    # 2. move_span contributes a source splice and a destination splice.
    for p in parsed:
        if p["kind"] != "move_span":
            continue
        frm = p["_from"]
        to = p["_to"]
        # Source is always deleted: move_span is a pure move, never
        # a copy or a replace. The model uses splice to put text
        # somewhere else.
        pl_src = _ensure(frm.path, frm.rev)
        pl_src.splices.append((frm.start, frm.count, []))

        # Destination splice.
        pl_dst = _ensure(to.path, to.rev)
        moved_body = pl_src.lines[frm.start - 1 : frm.start - 1 + frm.count]
        if p.get("moved_indent_delta"):
            moved_body = [
                _reindent(line, p["moved_indent_delta"]) for line in moved_body
            ]
        new_lines: list[str] = list(moved_body)

        if to.is_eof:
            # Append at end. Choose a start beyond current length.
            pl_dst.splices.append((len(pl_dst.lines) + 1, 0, new_lines))
        elif to.count == 0:
            # Point address: insert at line N, push existing N down.
            pl_dst.splices.append((to.start, 0, new_lines))
        else:
            # Span address: replace the M-line span starting at N with
            # the moved body.  Splice-replace semantics: lines [N, N+M)
            # are consumed and replaced with new_lines (no wrapper text).
            pl_dst.splices.append((to.start, to.count, new_lines))

    # 3. Validate and apply splices per file.
    for pl in plans.values():
        _apply_plan(pl)

    # 4. Write files.
    changed: list[dict] = []
    for pl in plans.values():
        p_abs = state.resolve(pl.path)
        if p_abs is None:
            continue
        old_text = _read_for_diff(p_abs)
        old_lines_list, old_had_nl = lines_with_endings(p_abs) if p_abs.is_file() else ([], False)
        write_lines(p_abs, pl.lines, pl.had_nl)
        new_text = p_abs.read_text(encoding="utf-8")
        if old_text == new_text:
            continue
        a, d = _line_delta(old_lines_list, pl.lines)
        new_rev = file_rev(p_abs)
        # Compact diff for echo back to the model.
        echo = _compact_diff(old_lines_list, pl.lines)
        # Prefix the echo with the new rev so the model can chain
        # follow-up edits on the just-written state via @rev:N.
        if echo:
            echo = f"@rev:{new_rev}\n{echo}"
        changed.append(
            {
                "path": pl.path,
                "added": a,
                "deleted": d,
                "rev_old": pl.rev,
                "rev_new": new_rev,
                "echo": echo,
            }
        )
        state.drop_spans_for(pl.path)

    return {
        "ok": True,
        "changed": changed,
        "diff_handle": state.new_diff_handle(),
    }


def _apply_plan(pl: FilePlan) -> None:
    # Validate bounds.
    n = len(pl.lines)
    for s, c, _ in pl.splices:
        if s < 1:
            raise _OpError(f"start must be >= 1 (got {s})")
        if c == 0:
            continue
        if s - 1 + c - 1 >= n:
            raise _OpError(
                f"edit at {pl.path} {s}+{c} exceeds file length {n}"
            )

    # Sort: descending by start, but preserve order for entries that
    # have the same start (so multiple inserts at the same line apply
    # in given op order — when applied descending, the *last* one in
    # the list becomes the topmost, so we keep the given order via
    # stable sort after a stable secondary key).
    indexed = list(enumerate(pl.splices))
    # When applying descending, earlier-issued inserts at the same
    # point should remain "earlier" in the file. The splice model is:
    # delete [s-1, s-1+c) and replace with new_lines. For inserts at
    # the same point (count=0), the one with the higher original
    # index should appear first in the output (top-most).
    # So we sort by (start DESC, index DESC) for non-zero-count, and
    # (start ASC, index ASC) for zero-count. To keep things simple in
    # v1, we treat multiple inserts at the same point as appended in
    # the order given — the user is responsible for ordering.
    sorted_splices = sorted(
        indexed,
        key=lambda kv: (-kv[1][0], -kv[1][1], -kv[0]),
    )
    for _, (s, c, new_lines) in sorted_splices:
        # After prior edits, the line indices may have shifted for
        # earlier (lower-start) ranges. But because we go in
        # descending-start order, lower-start edits have not yet been
        # applied, so the file lines for them are still at their
        # original positions. The high-start ones we just applied
        # consumed from the bottom of the file. So this is correct
        # in the original-coordinate regime.
        if c == 0:
            insert_at = s - 1  # 0-based insertion point
            # The line "s" must still exist; for EOF, s == len+1 is OK.
            if insert_at > len(pl.lines):
                raise _OpError(
                    f"insertion at {pl.path}: line {s} out of range"
                )
            pl.lines = pl.lines[:insert_at] + list(new_lines) + pl.lines[insert_at:]
        else:
            pl.lines = (
                pl.lines[: s - 1] + list(new_lines) + pl.lines[s - 1 + c :]
            )


def _read_for_diff(p_abs: Path) -> str:
    if not p_abs.is_file():
        return ""
    return p_abs.read_text(encoding="utf-8")


def _line_delta(old_lines: list[str], new_lines: list[str]) -> tuple[int, int]:
    """Real added/deleted line counts (not the net difference).

    A replace that swaps N lines for N lines reports (N, N), not (0, 0),
    so a substantive edit never looks like a no-op to the model.
    """
    import difflib

    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added = deleted = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


# Lines of unified-diff context shown around the change. Measured at 1
# line, the model still re-read after 84% of non-final edits anyway
# (see eval/runs analysis); bumped to 2 so the echo carries enough
# surrounding context to stand in for a fresh read_range.
_ECHO_CONTEXT = 2


def _compact_diff(old_lines: list[str], new_lines: list[str]) -> str:
    """Render a unified-diff hunk for echo back to the model.

    Standard unified-diff syntax with 2 lines of context. Real hunk splitting
    is preserved; long pure addition or deletion blocks may be compacted to
    first line, marker, last line. Replacement blocks are left verbatim.
    """
    return unified_diff_hunks(
        old_lines,
        new_lines,
        context=_ECHO_CONTEXT,
        compact=True,
    )





def _split_text_lines(text: str) -> list[str]:
    if text == "":
        return []
    had = text.endswith("\n")
    body = text[:-1] if had else text
    return body.split("\n")


def _reindent(line: str, delta: int) -> str:
    """Add ``delta`` spaces of leading indentation.

    A negative delta is a best-effort trim of up to ``|delta|`` leading
    spaces. Tabs in the leading whitespace are left intact (we only
    touch spaces per the spec).
    """
    if delta == 0:
        return line
    # Count leading spaces only.
    i = 0
    while i < len(line) and line[i] == " ":
        i += 1
    leading = line[:i]
    rest = line[i:]
    if delta > 0:
        return (" " * delta) + leading + rest
    trim = min(-delta, len(leading))
    return leading[trim:] + rest


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

# Per-op semantics (count=0/1/N, exact destination forms, address
# syntax) are documented once in prompts/span_tools.md, which is sent
# every turn alongside this schema. The schema stays terse to avoid
# paying for the same guidance twice in the per-turn token footprint.
EDIT_BATCH_TOOL = {
    "name": "edit_batch",
    "description": (
        "Apply span edits atomically (splice, move_span, replace_word). "
        "Snapshot line numbers. Ops in a batch must not overlap. "
        "For a single-line replacement use splice with count=1. "
        "Path edit addresses require +count; span addresses may be S<id>:line+count."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "atomic": {"type": "boolean", "default": True},
            "coords": {"type": "string", "enum": ["snapshot"], "default": "snapshot"},
            "verify": {"type": "boolean", "default": False, "description": "if true, run the build check (run_check) right after a SUCCESSFUL edit, in this same turn; set true only when you want a build pass, false while you still have edits to make"},
            "ops": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "op": {"const": "splice"},
                                "at": {"type": "string", "description": "path:line+count (+count mandatory) or S<id>:line+count; count=0 inserts only, count=1 cuts/changes one line"},
                                "text": {"type": "string", "description": "replacement/inserted text; empty deletes"},
                            },
                            "required": ["op", "at"],
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "op": {"const": "move_span"},
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "moved_indent_delta": {"type": "integer"},
                            },
                            "required": ["op", "from", "to"],
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "op": {"const": "replace_word"},
                                "paths": {"type": "array", "items": {"type": "string"}},
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "boundary": {"type": "string", "enum": ["cpp_identifier"]},
                                "expected": {
                                    "type": "object",
                                    "properties": {
                                        "exact": {"type": "integer"},
                                        "min": {"type": "integer"},
                                    },
                                },
                                "include": {
                                    "type": "string",
                                    "enum": ["all_text", "code_and_comments", "code_only"],
                                },
                                "dry_run": {"type": "boolean"},
                            },
                            "required": ["op", "paths", "from", "to"],
                        },
                    ]
                },
            },
        },
        "required": ["ops"],
    },
}
