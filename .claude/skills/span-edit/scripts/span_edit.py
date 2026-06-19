#!/usr/bin/env python3
"""span_edit — line-addressed structural edits for large refactors.

A standalone reimplementation of the eval harness `edit_batch` span tools
(splice / move_span / replace_word) so they can be driven from a normal
shell. Designed for the edits a unified-diff or hand-retype does badly:
moving a >10-line block, extracting a function, or renaming an identifier
across files.

Addressing (snapshot / pre-edit line numbers, 1-based):

  L+N    N lines starting at line L     (e.g. 48+12 = lines 48..59)
  L+0    insertion point before line L  (insert only; needs text)
  EOF    end of file                    (move destination only)

All line numbers refer to the file as it is on disk BEFORE this command
runs. A single command may emit several internal splices (a move is a
delete + an insert); they are all resolved in snapshot coords and applied
bottom-up, so earlier line numbers never shift under you.

Subcommands:

  move    <file> --from L+N --to DEST [--to-file F] [--indent D] [--dry-run]
  splice  <file> --at L+N [--text S | --text-file F | -] [--dry-run]
  rename  --from OLD --to NEW --paths a b ... [--expect N] [--dry-run]

On success each prints a compact unified diff and the new line counts.
Exit code is non-zero on any error (bad range, stale text, count guard).
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------
# File IO (preserve trailing-newline state, like the harness)
# ---------------------------------------------------------------------


def read_lines(path: Path) -> tuple[list[str], bool]:
    """Return (lines_without_endings, had_trailing_newline)."""
    if not path.is_file():
        die(f"file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text == "":
        return [], False
    had_nl = text.endswith("\n")
    body = text[:-1] if had_nl else text
    return body.split("\n"), had_nl


def write_lines(path: Path, lines: list[str], had_nl: bool) -> None:
    text = "\n".join(lines)
    if had_nl and lines:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def split_text(text: str | None) -> list[str]:
    if not text:
        return []
    had = text.endswith("\n")
    body = text[:-1] if had else text
    return body.split("\n")


# ---------------------------------------------------------------------
# Address parsing:  L+N  |  L+0  |  EOF
# ---------------------------------------------------------------------


_ADDR = re.compile(r"^(?P<line>\d+)\+(?P<count>\d+)$")


def parse_addr(spec: str, n_lines: int, allow_eof: bool = False) -> tuple[int, int, bool]:
    """Return (start, count, is_eof). start/count 1-based snapshot coords."""
    s = spec.strip()
    if s == "EOF":
        if not allow_eof:
            die("EOF address is only valid as a move destination")
        return n_lines + 1, 0, True
    m = _ADDR.match(s)
    if not m:
        die(f"bad address {spec!r}; use L+N (e.g. 48+12), L+0 to insert, or EOF")
    start = int(m.group("line"))
    count = int(m.group("count"))
    if start < 1:
        die(f"line must be >= 1 (got {start})")
    if count == 0:
        # insertion point: before line `start`; start may be n+1 (append)
        if start > n_lines + 1:
            die(f"insert point {start} past end of file ({n_lines} lines)")
    else:
        if start + count - 1 > n_lines:
            die(f"range {start}+{count} exceeds file length {n_lines}")
    return start, count, False


# ---------------------------------------------------------------------
# Splice engine: apply (start, count, new_lines) triples in snapshot
# coords, bottom-up so lower line numbers stay valid.
# ---------------------------------------------------------------------


def apply_splices(lines: list[str], splices: list[tuple[int, int, list[str]]]) -> list[str]:
    out = list(lines)
    # Descending by start so earlier edits don't shift later indices.
    for start, count, new in sorted(splices, key=lambda x: (-x[0], -x[1])):
        i = start - 1
        if count == 0:
            out = out[:i] + list(new) + out[i:]
        else:
            out = out[:i] + list(new) + out[i + count:]
    return out


def reindent(line: str, delta: int) -> str:
    if delta == 0:
        return line
    i = 0
    while i < len(line) and line[i] == " ":
        i += 1
    leading, rest = line[:i], line[i:]
    if delta > 0:
        return (" " * delta) + line
    trim = min(-delta, len(leading))
    return leading[trim:] + rest


# ---------------------------------------------------------------------
# Identifier (whole-word) replace, C/C++ boundary
# ---------------------------------------------------------------------


def word_pattern(word: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])")


# ---------------------------------------------------------------------
# Diff echo + reporting
# ---------------------------------------------------------------------


def compact_diff(path: str, old: list[str], new: list[str]) -> str:
    diff = difflib.unified_diff(
        old, new, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2
    )
    return "\n".join(diff)


def counts(old: list[str], new: list[str]) -> tuple[int, int]:
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    added = deleted = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def emit(path: str, old: list[str], new: list[str], dry_run: bool, had_nl: bool) -> None:
    if old == new:
        print("no change")
        return
    diff = compact_diff(path, old, new)
    print(diff)
    added, deleted = counts(old, new)
    tag = "[dry-run] " if dry_run else ""
    print(f"\n{tag}{path}: +{added} -{deleted}  ({len(new)} lines)")
    if not dry_run:
        write_lines(Path(path), new, had_nl)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------


def cmd_splice(args) -> None:
    path = Path(args.file)
    lines, had_nl = read_lines(path)
    start, count, _ = parse_addr(args.at, len(lines))

    text = read_text_arg(args)
    if count == 0 and not text:
        die("L+0 is insert-only and needs --text/--text-file; use L+N with empty text to delete")
    new_body = split_text(text)

    new = apply_splices(lines, [(start, count, new_body)])
    emit(args.file, lines, new, args.dry_run, had_nl)


def cmd_move(args) -> None:
    src_path = Path(args.file)
    src_lines, src_nl = read_lines(src_path)
    fstart, fcount, _ = parse_addr(args.src, len(src_lines))
    if fcount == 0:
        die("move --from needs a real span (L+N, N>=1), not an insert point")

    dst_file = args.to_file or args.file
    same_file = Path(dst_file).resolve() == src_path.resolve()

    body = src_lines[fstart - 1 : fstart - 1 + fcount]
    if args.indent:
        body = [reindent(l, args.indent) for l in body]

    if same_file:
        dst_lines = src_lines
        dn = len(dst_lines)
    else:
        dst_lines, dst_nl = read_lines(Path(dst_file))
        dn = len(dst_lines)

    dstart, dcount, is_eof = parse_addr(args.dest, dn, allow_eof=True)

    if same_file:
        # Source delete + destination insert/replace, both in snapshot coords.
        splices = [(fstart, fcount, [])]
        if is_eof:
            splices.append((len(src_lines) + 1, 0, body))
        else:
            if dstart >= fstart and dstart < fstart + fcount and dcount == 0:
                # Destination inside the source: land it where the block was.
                splices.append((fstart, 0, body))
            else:
                splices.append((dstart, dcount, body))
        new = apply_splices(src_lines, splices)
        emit(args.file, src_lines, new, args.dry_run, src_nl)
    else:
        # Two files: delete from source, insert/replace in dest.
        new_src = apply_splices(src_lines, [(fstart, fcount, [])])
        if is_eof:
            dsp = (len(dst_lines) + 1, 0, body)
        else:
            dsp = (dstart, dcount, body)
        new_dst = apply_splices(dst_lines, [dsp])
        emit(args.file, src_lines, new_src, args.dry_run, src_nl)
        print()
        emit(dst_file, dst_lines, new_dst, args.dry_run, dst_nl)


def cmd_rename(args) -> None:
    pat = word_pattern(args.src)
    total = 0
    plans: list[tuple[str, list[str], list[str], bool]] = []
    for rel in args.paths:
        p = Path(rel)
        lines, had_nl = read_lines(p)
        new_lines = []
        for line in lines:
            new_lines.append(pat.sub(args.to, line))
        n = sum(len(pat.findall(line)) for line in lines)
        total += n
        plans.append((rel, lines, new_lines, had_nl))

    if args.expect is not None and total != args.expect:
        die(f"expected {args.expect} matches of {args.src!r}, found {total}")
    if total == 0:
        die(f"no whole-word matches of {args.src!r} in given paths")

    print(f"{args.src!r} -> {args.to!r}: {total} match(es) across {len(plans)} file(s)\n")
    for rel, old, new, had_nl in plans:
        if old != new:
            emit(rel, old, new, args.dry_run, had_nl)
            print()


# ---------------------------------------------------------------------
# Text-arg resolution for splice
# ---------------------------------------------------------------------


def read_text_arg(args) -> str | None:
    sources = [args.text is not None, args.text_file is not None, args.stdin]
    if sum(bool(s) for s in sources) > 1:
        die("give at most one of --text / --text-file / -")
    if args.text is not None:
        return args.text
    if args.text_file is not None:
        return Path(args.text_file).read_text(encoding="utf-8")
    if args.stdin:
        return sys.stdin.read()
    return None  # delete


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="span_edit",
        description="Line-addressed structural edits (move / splice / rename).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("move", help="move a span of lines (refactor / extract)")
    m.add_argument("file", help="source file")
    m.add_argument("--from", dest="src", required=True, metavar="L+N", help="source span")
    m.add_argument("--to", dest="dest", required=True, metavar="DEST", help="destination L+0 (insert), L+N (replace), or EOF")
    m.add_argument("--to-file", dest="to_file", help="destination file (default: same as source)")
    m.add_argument("--indent", type=int, default=0, metavar="D", help="add/trim D leading spaces on moved lines")
    m.add_argument("--dry-run", action="store_true")
    m.set_defaults(func=cmd_move)

    s = sub.add_parser("splice", help="replace / insert / delete a line range")
    s.add_argument("file")
    s.add_argument("--at", required=True, metavar="L+N", help="L+0 insert, L+N replace/delete")
    s.add_argument("--text", help="replacement/inserted text")
    s.add_argument("--text-file", dest="text_file", help="read text from a file")
    s.add_argument("-", dest="stdin", action="store_true", help="read text from stdin")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_splice)

    r = sub.add_parser("rename", help="whole-word identifier rename across files")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--to", required=True)
    r.add_argument("--paths", nargs="+", required=True)
    r.add_argument("--expect", type=int, help="fail unless exactly N matches found")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_rename)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
