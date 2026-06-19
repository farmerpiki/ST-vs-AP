"""Run checks and oracle verification for a finished worktree."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .check_output import summarize_check_output
from .util import diff_stat, diff_text, truncate_lines


def run_checks(worktree: Path, checks: list[dict]) -> tuple[bool, list[dict]]:
    """Execute each named check, return (all_passed, per_check_results)."""
    results: list[dict] = []
    all_ok = True
    for c in checks:
        name = c.get("name", "?")
        cmd = c.get("cmd", "")
        try:
            proc = subprocess.run(
                ["bash", "-lc", cmd],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            summary = summarize_check_output(out, proc.returncode, worktree)
            ok = bool(summary["build_ok"])
            if c.get("require_tests_passed"):
                ok = ok and bool(summary.get("tests_passed"))
            stdout, stdout_truncated = truncate_lines(proc.stdout or "", 80)
            stderr, stderr_truncated = truncate_lines(proc.stderr or "", 80)
        except subprocess.TimeoutExpired:
            ok = False
            proc = None  # type: ignore[assignment]
            summary = {"verdict": "timeout", "build_ok": False}
            stdout = stderr = ""
            stdout_truncated = stderr_truncated = False
        all_ok = all_ok and ok
        results.append(
            {
                "name": name,
                "ok": ok,
                "exit_code": getattr(proc, "returncode", -1) if proc else -1,
                "verdict": summary.get("verdict"),
                "summary": summary,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        )
    return all_ok, results


def verify_oracle(worktree: Path, oracle: dict, allowed_paths: list[str]) -> dict:
    """Verify required/forbidden text and path constraints.

    ``allowed_paths`` is a list of repo-relative prefixes. The verifier
    rejects any change outside those prefixes.
    """
    changed, _ = diff_stat(worktree)
    changed_set = set(changed)
    allowed_set = {p.replace("\\", "/").rstrip("/") for p in allowed_paths}
    out_of_scope: list[str] = []
    for p in changed_set:
        ok = any(p == a or p.startswith(a + "/") for a in allowed_set)
        if not ok:
            out_of_scope.append(p)
    new_files: list[str] = []  # for v1: not detected separately; assume
    # any new file would appear in diff_stat with all lines as additions.

    required_contains: list[dict] = oracle.get("required_contains", []) or []
    forbidden_contains: list[dict] = oracle.get("forbidden_contains", []) or []
    # New oracle check kinds: regex match and positional ordering.
    # - required_regex: pattern must match somewhere in the file
    # - required_position: `before` text must appear before `after` text
    required_regex: list[dict] = oracle.get("required_regex", []) or []
    required_position: list[dict] = oracle.get("required_position", []) or []

    import re
    missing: list[dict] = []
    present_forbidden: list[dict] = []
    for item in required_contains:
        path = item.get("path")
        text = item.get("text")
        if not path or text is None:
            continue
        p = worktree / path
        if not p.is_file():
            missing.append({"path": path, "text": text, "reason": "file_missing"})
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        if text not in body:
            missing.append({"path": path, "text": text, "reason": "text_absent"})
    for item in required_regex:
        path = item.get("path")
        pattern = item.get("pattern")
        if not path or pattern is None:
            continue
        p = worktree / path
        if not p.is_file():
            missing.append({"path": path, "text": pattern, "reason": "file_missing"})
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        if not re.search(pattern, body):
            missing.append({"path": path, "text": pattern, "reason": "pattern_absent"})
    for item in required_position:
        path = item.get("path")
        before = item.get("before")
        after = item.get("after")
        before_re = item.get("before_re")
        after_re = item.get("after_re")
        if not path or (before is None and before_re is None) or (after is None and after_re is None):
            continue
        p = worktree / path
        label_before = before if before is not None else before_re
        label_after = after if after is not None else after_re
        if not p.is_file():
            missing.append({"path": path, "text": f"{label_before!r} before {label_after!r}", "reason": "file_missing"})
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        # `before`/`after` are matched as regex when the item specifies
        # `before_re`/`after_re`; otherwise literal substring match.
        if before_re is not None:
            m = re.search(before_re, body)
            b_idx = (m.start(1) if m and m.groups() else m.start()) if m else -1
        else:
            b_idx = body.find(before)
        if after_re is not None:
            m = re.search(after_re, body)
            a_idx = (m.start(1) if m and m.groups() else m.start()) if m else -1
        else:
            a_idx = body.find(after)
        if b_idx < 0:
            missing.append({"path": path, "text": label_before, "reason": "before_absent"})
            continue
        if a_idx < 0:
            missing.append({"path": path, "text": label_after, "reason": "after_absent"})
            continue
        if b_idx > a_idx:
            missing.append({
                "path": path,
                "text": f"{label_before!r} before {label_after!r}",
                "reason": "wrong_order",
            })
    for item in forbidden_contains:
        path = item.get("path")
        text = item.get("text")
        if not path or text is None:
            continue
        p = worktree / path
        if not p.is_file():
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        if text in body:
            present_forbidden.append({"path": path, "text": text})

    return {
        "checks_passed": True,  # filled in by run_checks elsewhere
        "allowed_paths_ok": not out_of_scope,
        "out_of_scope": out_of_scope,
        "required_contains_ok": not missing,
        "missing_required": missing,
        "forbidden_contains_ok": not present_forbidden,
        "present_forbidden": present_forbidden,
    }
