"""Compact build/test output parsing shared by tools and final checks."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def summarize_check_output(output: str, exit_code: int, worktree: Optional[Path] = None) -> dict:
    """Return a compact, model-facing summary for a check command.

    The eval check commands usually print EXIT_CFG/EXIT_BUILD/EXIT_TEST plus
    tiny_tests' "checks=N failures=M" line. Prefer those markers over the
    process exit code so test failures do not get misreported as compile
    failures when configure/build were clean.
    """
    out = output or ""
    diagnostics: list[dict] = []
    failures: list[dict] = []
    errors = 0
    failed_tests = 0
    tests_passed = False
    checks_summary: str | None = None
    exit_markers: dict[str, int] = {}
    out_was_empty = not out.strip()

    for ln in out.splitlines():
        s = ln.strip()
        for key, val in re.findall(r"\bEXIT_([A-Z_]+)=(-?\d+)\b", s):
            exit_markers[key.lower()] = int(val)

        if s.startswith("FAIL"):
            failed_tests += 1
            failures.append(_parse_fail_line(s, worktree))

        m = re.match(r"^(.+?\.\w+):(\d+):(\d+):\s*error:\s*(.*)$", s)
        if m:
            errors += 1
            diagnostics.append(
                {
                    "path": _rel_path(m.group(1), worktree),
                    "line": int(m.group(2)),
                    "col": int(m.group(3)),
                    "message": m.group(4).strip(),
                }
            )

        m2 = re.match(r"^checks=(\d+)\s+failures=(\d+)$", s)
        if m2:
            checks_summary = f"checks={m2.group(1)} failures={m2.group(2)}"
            failed_tests = max(failed_tests, int(m2.group(2)))
            tests_passed = int(m2.group(2)) == 0

    marker_build_ok = None
    if "cfg" in exit_markers or "build" in exit_markers:
        marker_build_ok = (
            exit_markers.get("cfg", 0) == 0 and exit_markers.get("build", 0) == 0
        )
    build_ok = (marker_build_ok if marker_build_ok is not None else exit_code == 0)
    build_ok = bool(build_ok and errors == 0 and not out_was_empty)

    all_passed = build_ok and tests_passed and checks_summary is not None
    if not build_ok:
        verdict = "unknown" if out_was_empty else "build_error"
    elif checks_summary is not None and not tests_passed:
        verdict = "tests_failed"
    elif all_passed:
        verdict = "all_passed"
    else:
        verdict = "pass"

    summary = {
        "build_ok": build_ok,
        "compile_errors": errors,
        "test_failures": failed_tests,
        "tests_passed": tests_passed,
        "checks_summary": checks_summary,
        "output_was_empty": out_was_empty,
        "exit_markers": exit_markers,
        "verdict": verdict,
        "all_passed": all_passed,
    }
    if diagnostics:
        summary["diagnostics"] = diagnostics[:20]
    if failures:
        summary["failures"] = failures[:20]
    return summary


_CMAKE_STATUS = re.compile(r"^-- ")


def clean_build_output(output: str, worktree: Optional[Path] = None) -> str:
    """Trim noise from a raw build log before showing it to the model.

    Drops cmake status/config lines (``-- ...``: compiler detection,
    configuring/generating done, "Build files have been written to ...") and
    rewrites the absolute worktree path to a relative one, since that long
    prefix is repeated in the FAILED compile command and every diagnostic.
    """
    if not output:
        return output
    prefix = None
    if worktree:
        try:
            prefix = str(Path(worktree).resolve())
        except Exception:
            prefix = str(worktree)
    kept: list[str] = []
    for ln in output.splitlines():
        if _CMAKE_STATUS.match(ln.strip()):
            continue
        if prefix:
            ln = ln.replace(prefix + "/", "").replace(prefix, ".")
        kept.append(ln)
    return "\n".join(kept)


def _parse_fail_line(line: str, worktree: Optional[Path]) -> dict:
    # tiny_tests prints: FAIL /path/file.cpp:16 Expr text
    m = re.match(r"^FAIL\s+(.+?\.\w+):(\d+)\s*(.*)$", line)
    if not m:
        return {"text": line[:300]}
    return {
        "path": _rel_path(m.group(1), worktree),
        "line": int(m.group(2)),
        "message": m.group(3).strip()[:300],
    }


def _rel_path(path: str, worktree: Optional[Path]) -> str:
    if not worktree:
        return path
    try:
        p = Path(path)
        if p.is_absolute():
            return p.resolve().relative_to(worktree.resolve()).as_posix()
    except Exception:
        pass
    return path
