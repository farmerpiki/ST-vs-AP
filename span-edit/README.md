# span-edit skill (distributable)

Line-addressed structural edits (`move` / `splice` / `rename`) for large
refactors and >10-line edits. Self-contained: `SKILL.md` + a stdlib-only
`scripts/span_edit.py`, no dependencies.

## Install

```bash
./install.sh            # interactive menu
./install.sh claude     # user-level Claude skill  -> ~/.claude/skills
./install.sh codex      # user-level Codex skill   -> ~/.codex/skills
./install.sh folder .   # project-local           -> ./.span-edit
```

Each target copies the skill into place and adds an idempotent pointer to the
relevant agent instructions file (`CLAUDE.md` for Claude, `AGENTS.md` for
Codex; both for a folder install). Re-running never duplicates the note.

See `SKILL.md` for usage and the when-to-use-it decision rule.
