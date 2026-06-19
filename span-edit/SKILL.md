---
name: span-edit
description: >-
  Line-addressed structural edits for big code moves. Use INSTEAD of Edit/Write
  whenever the change is a large refactor (moving code around, extracting a
  function, reordering blocks) OR a large edit where the cut/moved/replaced part
  of a function is more than 10 lines. Trigger phrases: "move this function",
  "extract", "pull out", "reorder", "relocate", "refactor", "rename across
  files", or any edit whose removed/relocated region exceeds ~10 lines.
---

# span-edit — structural edits by line address

For big structural changes, retyping a block through `Edit` or rewriting a file
with `Write` is where edits go wrong: indentation drifts, a brace is dropped, an
unrelated line gets clobbered. This skill moves/splices/renames by **line
address** instead — the block is relocated verbatim, never retyped.

The tool is `scripts/span_edit.py` (stdlib only, no deps). Three ops mirror the
eval harness span tools: `move`, `splice`, `rename`.

## When to reach for this (vs. Edit/Write)

Use span-edit when ANY of these hold:

- **Large refactor** — moving code around, extracting a function, reordering or
  relocating blocks, hoisting/sinking a definition.
- **Large edit** — you move, replace, cut, or extract part of a function and the
  affected region is **more than 10 lines**.
- **Identifier rename** across one or more files.

Keep using normal `Edit` for small, in-place changes (≤10 lines, no relocation):
fix a condition, tweak a return, edit a few adjacent lines. Don't force those
through this tool.

## The one rule: addresses are snapshot line numbers

Every address is a **1-based line number in the file as it is on disk right
now**, before the command runs. So **`Read` the file first** to get current line
numbers, then address against them. A single command may internally do several
edits (a move is a delete + an insert); they are all resolved in pre-edit
coordinates and applied bottom-up, so line numbers never shift under you.

Address forms:

| form  | meaning                                              |
|-------|------------------------------------------------------|
| `L+N` | N lines starting at line L (e.g. `48+12` = 48..59)   |
| `L+0` | insertion point **before** line L (insert only)      |
| `EOF` | end of file (valid only as a move destination)       |

## Ops

Run from the repo root with paths relative to it. Add `--dry-run` to any command
to preview the unified diff without writing — do this first when unsure.

### move — relocate a block (the refactor workhorse)

```bash
python3 .claude/skills/span-edit/scripts/span_edit.py move <file> \
    --from L+N --to DEST [--to-file OTHER] [--indent D] [--dry-run]
```

- `--from L+N` source span (always removed — `move` is never a copy).
- `--to DEST` destination, one of:
  - `M+0` insert before line M (existing M pushed down),
  - `M+K` replace the K-line span at M with the moved body,
  - `EOF` append at end of file.
- `--to-file OTHER` move into a different file (extract to another translation
  unit). Default is the same file.
- `--indent D` add D leading spaces to every moved line (negative trims), for
  when the block changes nesting depth.

Extract a 14-line helper out of `parser.cpp` and append it to `util.cpp`:

```bash
... move src/parser.cpp --from 60+14 --to-file src/util.cpp --to EOF
```

### splice — replace / insert / delete a line range

```bash
python3 .claude/skills/span-edit/scripts/span_edit.py splice <file> \
    --at L+N [--text S | --text-file F | -] [--dry-run]
```

- `--at L+N` (N≥1) replaces lines L..L+N-1 with the supplied text; empty text
  deletes them.
- `--at L+0` inserts text before line L (text required).
- Supply text via `--text "..."`, `--text-file path`, or `-` to read stdin
  (best for multi-line bodies via a heredoc).

Replace a 12-line function body (lines 40..51):

```bash
... splice src/stats.cpp --at 40+12 - <<'EOF'
double Mean(const std::vector<double>& xs) {
    ...
}
EOF
```

### rename — whole-word identifier rename across files

```bash
python3 .claude/skills/span-edit/scripts/span_edit.py rename \
    --from OLD --to NEW --paths a.cpp b.hpp ... [--expect N] [--dry-run]
```

C/C++ identifier boundaries (won't touch `OLD` inside a longer word). Pass
`--expect N` to fail loudly unless exactly N matches are found — use it as a
guard so a rename can't silently over- or under-apply.

## Output & verification

Each command prints a compact unified diff plus `+added -deleted (N lines)`.
Trust it — it reflects what was written; no need to re-`Read` to confirm. After a
structural change, build/run the project's tests to verify behavior. Any error
(range past EOF, `--expect` mismatch, insert with no text) exits non-zero and
writes nothing, so a failed command leaves the file untouched.

## Don'ts

- Don't pass absolute line numbers from a stale read — re-`Read` if the file
  changed since.
- Don't use `splice` to relocate a block (delete here, paste there); use `move`
  so the body is carried verbatim and the source is guaranteed removed.
- Don't reach for this on tiny edits — `Edit` is fewer moving parts there.
