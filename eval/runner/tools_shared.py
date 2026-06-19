"""Inspect tools shared by both variants.

These are intentionally limited: no arbitrary shell, hard output caps,
and cwd-bound paths.
"""
from __future__ import annotations

import json
import re
import subprocess

from .check_output import clean_build_output, summarize_check_output
from .state import ToolState
from .util import diff_stat, diff_text, lines_with_endings, truncate_lines


# ---------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------

_PATH_REV_TAIL = re.compile(r"@r[0-9a-f]{7}(?::\d+)?(?:\+\d+)?$")
_PATH_LINE_TAIL = re.compile(r":\d+(?:\+\d+)?$")


def normalize_path(path):
    """Strip a trailing `@<rev>`, `@<rev>:N`, or `:N` from a file path.

    Read tools (read_range, rg, diff) operate on the current file on
    disk and do not consume a rev. If a model supplies a path with a
    trailing `@<rev>:N` (confusing it with the edit_batch address
    syntax), we silently strip the rev/line tail rather than failing
    with `file_not_found`.

    Pure path forms like `src/parser.cpp` are returned unchanged.
    """
    if not isinstance(path, str) or not path:
        return path
    if "@" in path:
        path = _PATH_REV_TAIL.sub("", path)
    else:
        path = _PATH_LINE_TAIL.sub("", path)
    return path


def normalize_paths(paths):
    """Apply normalize_path to a list, dropping empty/non-string entries."""
    if not isinstance(paths, list):
        return paths
    return [normalize_path(p) for p in paths if isinstance(p, str) and p]


def _clean_rel_path(path):
    path = normalize_path(path)
    if not isinstance(path, str):
        return path
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _default_search_paths(state: ToolState) -> list[str]:
    return list(state.allowed_paths) if state.allowed_paths else ["."]


def _raw_arg(args: dict, key: str):
    raw = args.get("_raw") if isinstance(args, dict) else None
    if not isinstance(raw, str):
        return None
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if not m:
        return None
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)


def _raw_string_list(args: dict, key: str):
    raw = args.get("_raw") if isinstance(args, dict) else None
    if not isinstance(raw, str):
        return None
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[([^\]]*)\]', raw)
    if not m:
        return None
    vals = []
    for item in re.findall(r'"((?:\\.|[^"\\])*)"', m.group(1)):
        try:
            vals.append(json.loads('"' + item + '"'))
        except Exception:
            vals.append(item)
    return vals or None


def tool_status(args: dict, state: ToolState) -> dict:
    # Use git to report actual dirty files (modified, added, deleted,
    # untracked) rather than enumerating the whole worktree. We also
    # include all tracked files under a "tracked" list so the model
    # can confirm the worktree has source to edit when git reports
    # nothing dirty (i.e. before any edit). The "dirty" list uses
    # porcelain v1 format with rename arrows stripped.
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=state.worktree,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        return {"ok": False, "error": "git_status_failed", "detail": str(e)[:200]}
    paths: list[dict] = []
    for ln in out.stdout.splitlines():
        if not ln.strip():
            continue
        code = ln[:2]
        rest = ln[3:]
        if "->" in rest:
            rest = rest.split("->", 1)[1].strip()
        if code.strip() == "?":
            status = "untracked"
        elif code.strip() == "!":
            status = "ignored"
        else:
            status = {
                "M": "modified", "A": "added", "D": "deleted",
                "R": "renamed", "C": "copied", "U": "unmerged",
            }.get(code[1] if len(code) > 1 else code[0], "modified")
        paths.append({"path": rest.strip(), "status": status})
    # Always include the tracked-file list so the model can see the
    # worktree contents even when nothing is dirty yet.
    try:
        ls = subprocess.run(
            ["git", "ls-files"],
            cwd=state.worktree,
            capture_output=True,
            text=True,
            timeout=10,
        )
        tracked = [ln.strip() for ln in ls.stdout.splitlines() if ln.strip()]
    except Exception:
        tracked = []
    return {
        "ok": True,
        "cwd": str(state.worktree),
        "dirty": paths,
        "allowed_paths": list(state.allowed_paths),
        "check_names": [c.get("name") for c in state.checks if c.get("name")],
        "tracked": tracked,
    }


def tool_rg(args: dict, state: ToolState) -> dict:
    recovered: list[str] = []
    pattern = args.get("pattern", "")
    if not isinstance(pattern, str) or not pattern:
        raw_pattern = _raw_arg(args, "pattern")
        if raw_pattern:
            pattern = raw_pattern
            recovered.append("pattern")
    if not isinstance(pattern, str) or not pattern:
        return {"ok": False, "error": "missing_pattern"}
    raw_paths = args.get("paths")
    if raw_paths is None:
        raw_paths = _raw_string_list(args, "paths")
        if raw_paths:
            recovered.append("paths")
    if raw_paths is None:
        raw_paths = _default_search_paths(state)
    if isinstance(raw_paths, list):
        paths = [_clean_rel_path(p) for p in normalize_paths(raw_paths)]
    else:
        paths = _clean_rel_path(raw_paths)
    path_args = paths if isinstance(paths, list) else [paths]
    context = int(args.get("context", 0))
    max_matches = int(args.get("max_matches", 50))
    case_sensitive = bool(args.get("case_sensitive", False))
    word = bool(args.get("word", False))
    if max_matches > state.RG_MAX_MATCHES:
        max_matches = state.RG_MAX_MATCHES

    flags = ["-n", "--no-heading", "--color=never"]
    if not case_sensitive:
        flags.append("-i")
    if word:
        flags.append("-w")
    flags += [f"-C{context}"]
    flags += [pattern, "--", *path_args]
    try:
        out = subprocess.run(
            ["rg", *flags],
            cwd=state.worktree,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "rg_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "rg_timeout"}

    if out.returncode not in (0, 1):
        return {"ok": False, "error": "rg_failed", "stderr": out.stderr[:200]}

    raw_lines = out.stdout.splitlines()
    truncated = len(raw_lines) > max_matches
    kept = raw_lines[:max_matches]
    hits: list[dict] = []
    counter = 0
    # ripgrep with -n -Cn (no --column) prints:
    #   - match line (multi-file):  PATH:LINE:TEXT
    #   - match line (single-file): LINE:TEXT  (no path prefix)
    #   - context line:              LINE-TEXT
    #   - separator:                 --
    # We track the most recent path so context lines can be attributed.
    search_paths = [str(p) for p in path_args]
    single_path = search_paths[0] if len(search_paths) == 1 else ""
    current_path: str = ""
    for ln in kept:
        if ln == "--":
            continue
        # Multi-file match: "PATH:LINE:TEXT" (path has no colons;
        # ripgrep uses the first colon to separate path from line)
        m_multi = re.match(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<text>.*)$", ln)
        if m_multi:
            current_path = _clean_rel_path(m_multi.group("path"))
            counter += 1
            hits.append({
                "id": f"H{counter}",
                "path": current_path,
                "line": int(m_multi.group("line")),
                "text": m_multi.group("text"),
            })
            continue
        # Single-file match: "LINE:TEXT" (no path)
        m_single = re.match(r"^(?P<line>\d+):(?P<text>.*)$", ln)
        if m_single and (single_path or current_path):
            if single_path:
                current_path = _clean_rel_path(single_path)
            counter += 1
            hits.append({
                "id": f"H{counter}",
                "path": current_path,
                "line": int(m_single.group("line")),
                "text": m_single.group("text"),
            })
            continue
        # Context line: "LINE-TEXT"
        m_ctx = re.match(r"^(?P<line>\d+)-(?P<text>.*)$", ln)
        if m_ctx and current_path:
            counter += 1
            hits.append({
                "id": f"H{counter}",
                "path": current_path,
                "line": int(m_ctx.group("line")),
                "text": m_ctx.group("text"),
            })
            continue
    result = {"ok": True, "hits": hits, "truncated": truncated, "searched_paths": search_paths}
    if recovered:
        result["recovered_args"] = recovered
    return result


def tool_read_range(args: dict, state: ToolState) -> dict:
    """Read a range from a file. May also accept a hit reference."""
    if "hit" in args:
        # hit refers to a previously emitted rg hit. We don't store hits
        # in the state (rg output goes back to the model directly), so
        # we accept the textual fields as well.
        return {
            "ok": False,
            "error": "hit_lookup_not_supported_v1",
            "hint": "pass path/start/count instead of hit",
        }

    path = args.get("path")
    if not isinstance(path, str):
        return {"ok": False, "error": "missing_path"}
    path = _clean_rel_path(path)
    start = int(args.get("start", 1))
    count = int(args.get("count", state.READ_RANGE_DEFAULT_MAX))
    allow_large = bool(args.get("allow_large", False))
    if count > state.READ_RANGE_DEFAULT_MAX and not allow_large:
        count = state.READ_RANGE_DEFAULT_MAX
    if count <= 0:
        return {"ok": False, "error": "count_must_be_positive"}

    abs_path = state.resolve(path)
    if abs_path is None:
        return {"ok": False, "error": "path_outside_cwd"}
    if not abs_path.is_file():
        return {"ok": False, "error": "file_not_found", "path": path}
    lines, _ = lines_with_endings(abs_path)
    if start < 1:
        start = 1
    if start > len(lines):
        return {
            "ok": True,
            "path": path,
            "range": f"{start}+0",
            "text": "",
            "truncated": False,
        }
    end = min(start + count - 1, len(lines))
    block = lines[start - 1 : end]
    text = "\n".join(f"{i}|{line}" for i, line in enumerate(block, start=start))
    truncated = end - start + 1 < count and (start + count - 1) <= len(lines)
    span = state.new_span(path, start, end - start + 1)
    rel_count = end - start + 1
    return {
        "ok": True,
        "span": span.id,
        "path": path,
        "rev": span.rev,
        "range": f"{start}+{rel_count}",
        "start": start,
        "end": end,
        "hash": span.hash,
        "span_origin": f"{span.id}:1={path}:{start}",
        "addr_hint": (
            f"left labels are absolute file lines; use {path}:ABS+C. "
            f"Span form is relative: {span.id}:1={path}:{start}, "
            f"{span.id}:{rel_count}={path}:{end}."
        ),
        "text": text,
        "truncated": truncated,
    }


def tool_diff(args: dict, state: ToolState) -> dict:
    mode = args.get("mode", "stat")
    paths = args.get("paths")
    handle = state.new_diff_handle()
    if mode == "stat":
        # Use the helper from util.
        if isinstance(paths, list):
            paths = normalize_paths(paths)
        changed, stats = diff_stat(state.worktree)
        if isinstance(paths, list):
            wanted = set(paths)
            changed = [
                p for p in changed
                if any(p == w or p.startswith(w.rstrip("/") + "/") for w in wanted)
            ]
        items = []
        for p in changed:
            a, d = stats.get(p, (0, 0))
            items.append({"path": p, "added": a, "deleted": d})
        return {
            "ok": True,
            "mode": "stat",
            "changed": items,
            "diff_handle": handle,
        }
    if mode == "hunks":
        if isinstance(paths, list):
            paths = normalize_paths(paths)
        text = diff_text(state.worktree, paths, context=2)
        text, truncated = truncate_lines(text, state.DIFF_HUNK_MAX_LINES)
        return {
            "ok": True,
            "mode": "hunks",
            "diff_handle": handle,
            "diff": text,
            "truncated": truncated,
        }
    return {"ok": False, "error": "unknown_mode", "mode": mode}


def tool_run_check(args: dict, state: ToolState) -> dict:
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "missing_name"}
    entry = next((c for c in state.checks if c.get("name") == name), None)
    if entry is None:
        return {"ok": False, "error": "unknown_check", "name": name}
    cmd = entry.get("cmd", "")
    handle = state.new_output_handle()
    try:
        proc = subprocess.run(
            ["bash", "-lc", cmd],
            cwd=state.worktree,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "verdict": "timeout",
            "all_passed": False,
            "build_ok": False,
            "error": "timeout",
            "output_handle": handle,
            "truncated": False,
            "hint": "check timed out before producing a reliable result; inspect recent edits or rerun after reducing the cause of the hang",
        }
    out_full = (proc.stdout or "") + (proc.stderr or "")
    # Summary parses the RAW log (exit markers, absolute-path diagnostics);
    # the model-facing `output` is cleaned (cmake noise dropped, paths relative).
    out, truncated = truncate_lines(clean_build_output(out_full, state.worktree), state.RUN_CHECK_MAX_LINES)
    summary = summarize_check_output(out_full, proc.returncode, state.worktree)
    build_ok = bool(summary["build_ok"])
    verdict = summary["verdict"]
    all_passed = bool(summary["all_passed"])
    diagnostics = summary.get("diagnostics", [])
    failures = summary.get("failures", [])
    changed_paths, _ = diff_stat(state.worktree)
    changed_diag = [d for d in diagnostics if d.get("path") in changed_paths]
    result = {
        "ok": build_ok,
        "exit_code": proc.returncode,
        "verdict": verdict,
        "all_passed": all_passed,
        "build_ok": build_ok,
        "summary": summary,
        "diagnostics": diagnostics[:20],
        "failures": failures[:20],
        "output_handle": handle,
        "truncated": truncated,
    }
    if verdict == "build_error":
        result["changed_paths"] = changed_paths
        if changed_diag:
            result["changed_diagnostics"] = changed_diag[:8]
        result["hint"] = (
            "build_error must be fixed before final. Diagnostics in changed_paths are likely caused by this edit; "
            "read_range around the first diagnostic and repair the edited region."
        )
    elif verdict == "tests_failed":
        result["hint"] = "build ok; inspect failures, but tiny_cpp Median failures may be pre-existing unless this task is about Median."
    # Keep tests_failed compact: fail lines are enough; verbose=1 gets raw log.
    verbose = bool(args.get("verbose"))
    if verdict == "build_error" or verbose:
        result["output"] = out
    return result


def run_default_check(state: ToolState) -> dict | None:
    """Run the task's primary check (the build) for an edit's verify=true.

    Picks the check named "build" if present, else the first task check.
    Returns None when the task defines no checks.
    """
    names = [c.get("name") for c in state.checks if c.get("name")]
    if not names:
        return None
    name = "build" if "build" in names else names[0]
    return tool_run_check({"name": name}, state)


SHARED_TOOLS = {
    "status": {
        "name": "status",
        "description": "List dirty files (no content).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    "rg": {
        "name": "rg",
        "description": "Regex search. `pattern` is a regex string (required). Plain paths in `paths`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "integer", "default": 0},
                "max_matches": {"type": "integer", "default": 50},
                "case_sensitive": {"type": "boolean", "default": False},
                "word": {"type": "boolean", "default": False},
            },
            "required": ["pattern"],
        },
    },
    "read_range": {
        "name": "read_range",
        "description": "Read a line range; returns a span handle (S<id>). Plain path only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "minimum": 1, "default": 1},
                "count": {"type": "integer", "minimum": 1, "default": 120},
                "allow_large": {"type": "boolean", "default": False},
            },
            "required": ["path", "start"],
        },
    },
    "diff": {
        "name": "diff",
        "description": "Diff view: 'stat' (counts) or 'hunks' (patch text). Plain paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["stat", "hunks"], "default": "stat"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "integer", "default": 2, "description": "hunk context is fixed at 2 lines"},
            },
        },
    },
    "run_check": {
        "name": "run_check",
        "description": "Run a task-defined check (e.g. build, test). Name must be one of the task's checks.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "verbose": {"type": "boolean", "default": False}},
            "required": ["name"],
        },
    },
}

SHARED_TOOL_DISPATCH = {
    "status": tool_status,
    "rg": tool_rg,
    "read_range": tool_read_range,
    "diff": tool_diff,
    "run_check": tool_run_check,
}
