"""The ``apply_patch`` write tool for the baseline variant."""
from __future__ import annotations

from .apply_patch_lib import Hunk, PatchOp, apply_to_text, parse
from .state import ToolState
from .util import file_rev, unified_diff_hunks


def tool_apply_patch(args: dict, state: ToolState) -> dict:
    patch = args.get("patch", "")
    if not isinstance(patch, str) or not patch:
        return {"ok": False, "error": "missing_patch"}
    try:
        ops = parse(patch)
    except Exception as e:
        return {
            "ok": False,
            "error": "patch_parse_error",
            "details": str(e),
            "hint": (
                "Each hunk line must start with space=context, -=remove, +=add, blank=context, or @@. "
                "Use one Update File section with old/new hunk lines, not separate old/new sections."
            ),
        }

    # Validate all paths first.
    for op in ops:
        op.path = _clean_path(op.path)
        if op.action != "update":
            return {"ok": False, "error": "unsupported_op", "op": op.action}
        if not state.is_path_allowed(op.path):
            return {
                "ok": False,
                "error": "forbidden_path",
                "path": op.path,
            }
        p = state.resolve(op.path)
        if p is None:
            return {"ok": False, "error": "path_outside_cwd", "path": op.path}
        if not p.is_file():
            return {"ok": False, "error": "file_not_found", "path": op.path}

    # Apply.
    changed: list[dict] = []
    for op in ops:
        p = state.resolve(op.path)
        assert p is not None
        old_rev = file_rev(p)
        text = p.read_text(encoding="utf-8")
        normalized_marker_space = False
        try:
            new_text = apply_to_text(op, text)
        except Exception as e:
            normalized = _strip_extra_marker_space(op)
            if normalized is not None:
                try:
                    new_text = apply_to_text(normalized, text)
                    op = normalized
                    normalized_marker_space = True
                except Exception:
                    detail = _patch_debug(op, text)
                    return {
                        "ok": False,
                        "error": "patch_did_not_apply",
                        "path": op.path,
                        "details": str(e),
                        **detail,
                    }
            else:
                detail = _patch_debug(op, text)
                return {
                    "ok": False,
                    "error": "patch_did_not_apply",
                    "path": op.path,
                    "details": str(e),
                    **detail,
                }
        p.write_text(new_text, encoding="utf-8")
        new_rev = file_rev(p)
        # Compute added/deleted for this file via line diff against old.
        a, d = _line_delta(text, new_text)
        echo = _compact_diff(_split_text(text), _split_text(new_text))
        item = {
            "path": op.path,
            "added": a,
            "deleted": d,
            "rev_old": old_rev,
            "rev_new": new_rev,
        }
        if echo:
            item["echo"] = f"@rev:{new_rev}\n{echo}"
        if normalized_marker_space:
            item["normalized_marker_space"] = True
        changed.append(item)
        state.drop_spans_for(op.path)

    # No-op detection: if any op was applied but the file's rev
    # didn't change, the patch had a remove+add of the same content
    # (or no -/+ lines at all). Tell the model it made no change so
    # it can correct the patch instead of assuming success.
    no_ops = [c for c in changed if c["rev_old"] == c["rev_new"] and c["added"] == 0 and c["deleted"] == 0]
    handle = state.new_diff_handle()
    result = {
        "ok": True,
        "changed": changed,
        "diff_handle": handle,
    }
    if no_ops:
        result["ok"] = False
        result["error"] = "patch_made_no_change"
        result["details"] = (
            "patch had -/+ or context lines but the resulting file is byte-identical to the original. "
            "Check that the + line actually differs from the - line."
        )
        result["no_change_paths"] = [c["path"] for c in no_ops]
    # verify=true runs the build right after a SUCCESSFUL patch, in the same
    # turn. Skip when the patch failed or made no change (ok already false).
    if result.get("ok") and args.get("verify"):
        from .tools_shared import run_default_check
        vr = run_default_check(state)
        if vr is not None:
            result["verify"] = vr
    return result


def _clean_path(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _patch_debug(op: PatchOp, text: str) -> dict:
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    for h in op.hunks:
        for wanted in h.old:
            if not wanted:
                continue
            exact = [i + 1 for i, line in enumerate(lines) if line == wanted][:3]
            if exact:
                continue
            near = [
                {"line": i + 1, "text": line}
                for i, line in enumerate(lines)
                if line.strip() == wanted.strip()
            ][:3]
            if near:
                return {
                    "hint": (
                        "Whitespace mismatch. After the leading patch marker (-/+/' '), copy the exact file line; "
                        "the marker is not indentation. Try old/context examples from patch_line_examples."
                    ),
                    "wanted": wanted,
                    "near": near,
                    "patch_line_examples": [
                        {"remove": "-" + near[0]["text"], "context": " " + near[0]["text"]}
                    ],
                }
        if h.old and not _is_contiguous_anywhere(lines, h.old):
            return {
                "hint": (
                    "Every old/context line matches the file individually, but not together as one "
                    "contiguous, in-order block — the hunk lines are out of order, duplicated, or mixed "
                    "from different parts of the file. apply_patch requires `old` to be an exact "
                    "contiguous slice of the current file, top to bottom. Re-read just the target lines "
                    "with read_range and copy them verbatim and in order; prefer a minimal hunk (only the "
                    "changed line(s), 0 lines of context) over adding surrounding context."
                ),
                "old_lines": h.old,
            }
    return {
        "hint": "Old/context lines must exactly match current file. Re-read a small range and retry with exact context.",
    }


def _is_contiguous_anywhere(lines: list[str], pat: list[str]) -> bool:
    n = len(pat)
    if n == 0 or n > len(lines):
        return False
    return any(lines[i : i + n] == pat for i in range(len(lines) - n + 1))


def _split_text(text: str) -> list[str]:
    if text == "":
        return []
    body = text[:-1] if text.endswith("\n") else text
    return body.split("\n")


def _strip_extra_marker_space(op: PatchOp) -> PatchOp | None:
    """Recover the common mistake of adding one space after -/+ markers.

    If the model copied read_range output and wrote ``-     foo`` for a
    four-space-indented line, the parsed hunk wants five spaces. Try the
    same patch with one leading space removed from every non-empty hunk
    line. The caller only uses this if the normalized patch applies cleanly.
    """
    changed = False
    hunks: list[Hunk] = []
    for h in op.hunks:
        old = []
        new = []
        for line in h.old:
            if line.startswith(" "):
                old.append(line[1:])
                changed = True
            else:
                old.append(line)
        for line in h.new:
            if line.startswith(" "):
                new.append(line[1:])
                changed = True
            else:
                new.append(line)
        hunks.append(Hunk(old=old, new=new, has_removed=h.has_removed))
    if not changed:
        return None
    return PatchOp(action=op.action, path=op.path, hunks=hunks)


def _line_delta(old: str, new: str) -> tuple[int, int]:
    import difflib

    old_lines = _split_text(old)
    new_lines = _split_text(new)
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added = deleted = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def _compact_diff(old_lines: list[str], new_lines: list[str]) -> str:
    return unified_diff_hunks(old_lines, new_lines, context=2, compact=True)


APPLY_PATCH_TOOL = {
    "name": "apply_patch",
    "description": (
        "Apply a textual patch. Format: *** Begin Patch / *** Update "
        "File: path / leading-space=context / -removed / +added / "
        "*** End Patch. ONE Begin/End block per call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "Patch text"},
            "verify": {"type": "boolean", "default": False, "description": "if true, run the build check (run_check) right after the patch applies successfully, in this same turn; set true only when you want a build pass, false while you still have edits to make"},
        },
        "required": ["patch"],
    },
}
