"""Common helpers: hashing, path safety, worktrees, and rev computation."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def file_rev(abs_path: Path) -> str:
    """``r<sha1[:7]>`` for a file's current bytes."""
    if not abs_path.is_file():
        # Use empty content hash; never raise - tools should check existence.
        return "r" + sha1_text("")[:7]
    return "r" + sha1_text(abs_path.read_bytes().decode("utf-8", errors="replace"))[:7]


def line_hash(lines: list[str], start: int, count: int) -> str:
    block = lines[start - 1 : start - 1 + count]
    return sha1_text("\n".join(block))[:8]


def read_lines(path: Path) -> list[str]:
    """Read a text file preserving its lines (including trailing newline).

    The returned list contains 1-indexed semantics: ``lines[0]`` is line 1.
    The trailing newline, if any, is preserved as part of the last element.
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if text == "":
        return []
    # Split into lines preserving final newline state.
    if text.endswith("\n"):
        body, final = text[:-1], "\n"
    else:
        body, final = text, ""
    parts = body.split("\n")
    if final:
        parts.append("")  # the implicit final blank line is not real; skip
        # Actually: when text ends with \n, splitting on \n drops the empty
        # trailing element. To preserve line indices, model each newline as
        # a separator; the Nth split segment is the Nth line content.
    return parts


def lines_with_endings(path: Path) -> tuple[list[str], bool]:
    """Return (lines, had_trailing_newline).

    ``lines[i]`` corresponds to logical line i+1. The newline is not part
    of the stored content; trailing-newline state is returned separately.
    """
    if not path.is_file():
        return [], False
    text = path.read_text(encoding="utf-8")
    had_nl = text.endswith("\n")
    body = text[:-1] if had_nl else text
    return body.split("\n"), had_nl


def write_lines(path: Path, lines: list[str], had_trailing_newline: bool) -> None:
    body = "\n".join(lines)
    if had_trailing_newline:
        body += "\n"
    path.write_text(body, encoding="utf-8")


def is_cwd_relative(root: Path, target: Path) -> bool:
    """Return True if ``target`` is within ``root`` (no escape)."""
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_resolve(root: Path, rel: str) -> Optional[Path]:
    """Resolve a relative path against root, rejecting escapes."""
    if os.path.isabs(rel):
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def copy_fixture(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("build", ".cache"))


def git_init_and_commit(worktree: Path) -> str:
    """Initialize git in a worktree and create a baseline commit.

    Returns the commit SHA. Falls back to a no-op (returns empty string) if
    git is unavailable.
    """
    try:
        subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "config", "user.email", "eval@local"],
            cwd=worktree,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "eval"], cwd=worktree, check=True
        )
        subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "baseline"], cwd=worktree, check=True
        )
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def diff_stat(worktree: Path) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Compute (changed_paths, {path: (added, deleted)}).

    If git is unavailable, returns all files in the worktree as
    ``(len(content), 0)`` to give a rough view.
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--no-color", "--numstat"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        paths = []
        stats: dict[str, tuple[int, int]] = {}
        for p in worktree.rglob("*"):
            if p.is_file():
                rel = p.relative_to(worktree).as_posix()
                try:
                    added = len(p.read_text(encoding="utf-8").splitlines())
                except Exception:
                    added = 0
                paths.append(rel)
                stats[rel] = (added, 0)
        return sorted(paths), stats

    changed: list[str] = []
    stats: dict[str, tuple[int, int]] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, p = parts
        if a == "-" and d == "-":  # binary
            stats[p] = (0, 0)
        else:
            stats[p] = (int(a), int(d))
        changed.append(p)
    return sorted(changed), stats


def diff_text(worktree: Path, paths: Optional[list[str]] = None, context: int | None = None) -> str:
    args = ["git", "diff", "--no-color"]
    if context is not None:
        args.append(f"-U{max(0, int(context))}")
    if paths:
        args += ["--", *paths]
    try:
        out = subprocess.run(args, cwd=worktree, capture_output=True, text=True)
        return out.stdout
    except Exception:
        return ""


def truncate_lines(text: str, max_lines: int) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]) + f"\n... [truncated, {len(lines) - max_lines} more lines]", True


def _compact_run(run: list[str], sign: str) -> list[str]:
    """Collapse one contiguous run of same-sign diff lines.

    Keep the first and last line; replace the >= 3 in-between lines with a
    single ``<sign> [omitted compacted block]`` marker. Only do so when it
    actually saves space: if the omitted middle is smaller than the marker
    (e.g. a run of `}` / blank / `}`), leave the run verbatim.
    """
    if len(run) < 5:
        return run
    middle = run[1:-1]
    marker = f"{sign} [omitted compacted block]"
    if sum(len(x) for x in middle) <= len(marker):
        return run
    return [run[0], marker, run[-1]]


def compact_diff_runs(lines: list[str]) -> list[str]:
    """Compact pure addition/deletion change blocks in unified diff hunks.

    A change block is the non-context region between context lines inside a
    hunk. Compact only blocks that are entirely '+' or entirely '-'. Replacement
    blocks contain both signs and are left verbatim, even though difflib renders
    them as adjacent '-' and '+' runs.
    """
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("@@") or not line.startswith(("+", "-", " ")):
            out.append(line)
            i += 1
            continue
        if line.startswith(" "):
            out.append(line)
            i += 1
            continue

        j = i
        signs: set[str] = set()
        while j < n and lines[j].startswith(("+", "-")):
            signs.add(lines[j][:1])
            j += 1
        block = lines[i:j]
        if len(signs) == 1:
            sign = next(iter(signs))
            out.extend(_compact_run(block, sign))
        else:
            out.extend(block)
        i = j
    return out


def unified_diff_hunks(
    old_lines: list[str],
    new_lines: list[str],
    *,
    context: int = 2,
    compact: bool = True,
) -> str:
    """Render real unified-diff hunks without file headers.

    The output can contain multiple hunks exactly as ``difflib.unified_diff``
    emits them for the requested context. Optional compaction is limited by
    ``compact_diff_runs``.
    """
    import difflib

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        lineterm="",
        n=max(0, int(context)),
    ))
    if len(diff) >= 2 and diff[0].startswith("---") and diff[1].startswith("+++"):
        diff = diff[2:]
    if compact:
        diff = compact_diff_runs(diff)
    return "\n".join(diff)
