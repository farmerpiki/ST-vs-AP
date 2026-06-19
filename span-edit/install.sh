#!/usr/bin/env bash
# install.sh — install the span-edit skill for Claude, Codex, or a single folder.
#
# Usage:
#   ./install.sh                 # interactive menu
#   ./install.sh claude          # user-level Claude skill  (~/.claude/skills)
#   ./install.sh codex           # user-level Codex skill   (~/.codex/skills)
#   ./install.sh folder [PATH]   # project-local install in PATH (default: cwd)
#
# Every target copies SKILL.md + scripts/ into place and adds a one-line
# pointer to the relevant agent instructions file (CLAUDE.md / AGENTS.md),
# idempotently — re-running never duplicates the note.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="span-edit"
MARKER="<!-- span-edit-skill -->"

note_block() {
  # $1 = relative path to SKILL.md from the file's directory
  cat <<EOF
$MARKER
## span-edit skill

For large refactors and large edits — moving code around, extracting a
function, reordering blocks, or any edit whose cut/moved region is **more than
10 lines** — prefer the \`span-edit\` tool over hand edits. It relocates blocks
by line address instead of retyping them.

- Guide: \`$1\`
- Tool: \`$(dirname "$1")/scripts/span_edit.py\` (\`move\` / \`splice\` / \`rename\`, stdlib-only)
EOF
}

copy_skill() {
  # $1 = destination skill dir
  local dest="$1"
  mkdir -p "$dest/scripts"
  cp "$SRC_DIR/SKILL.md" "$dest/SKILL.md"
  cp "$SRC_DIR/scripts/span_edit.py" "$dest/scripts/span_edit.py"
  chmod +x "$dest/scripts/span_edit.py"
  echo "  copied skill -> $dest"
}

mention() {
  # $1 = agent-instructions file, $2 = relative path to SKILL.md from that file
  local file="$1" rel="$2"
  mkdir -p "$(dirname "$file")"
  if [ -f "$file" ] && grep -qF "$MARKER" "$file"; then
    echo "  $file already mentions span-edit — skipped"
    return
  fi
  { [ -s "$file" ] && printf '\n'; note_block "$rel"; } >>"$file"
  echo "  mentioned in $file"
}

install_claude() {
  local base="$HOME/.claude"
  copy_skill "$base/skills/$NAME"
  mention "$base/CLAUDE.md" "skills/$NAME/SKILL.md"
  echo "Installed as Claude user skill. Restart Claude Code to pick it up."
}

install_codex() {
  local base="$HOME/.codex"
  copy_skill "$base/skills/$NAME"
  mention "$base/AGENTS.md" "skills/$NAME/SKILL.md"
  echo "Installed as Codex user skill."
}

install_folder() {
  local target="${1:-$PWD}"
  target="$(cd "$target" && pwd)"
  copy_skill "$target/.$NAME"        # <folder>/.span-edit/{SKILL.md,scripts/}
  local rel=".$NAME/SKILL.md"
  # Mention in whichever agent files the folder uses; default to creating both
  # so Claude and Codex agents in this folder both see it.
  mention "$target/CLAUDE.md" "$rel"
  mention "$target/AGENTS.md" "$rel"
  echo "Installed into $target"
}

choose() {
  echo "Install the span-edit skill where?"
  echo "  1) Claude  — user-level (~/.claude/skills, mentioned in ~/.claude/CLAUDE.md)"
  echo "  2) Codex   — user-level (~/.codex/skills,  mentioned in ~/.codex/AGENTS.md)"
  echo "  3) Folder  — a single project (./.span-edit, mentioned in its CLAUDE.md + AGENTS.md)"
  printf 'Choice [1-3]: '
  read -r choice
  case "$choice" in
    1) install_claude ;;
    2) install_codex ;;
    3) printf 'Target folder [%s]: ' "$PWD"; read -r path; install_folder "${path:-$PWD}" ;;
    *) echo "Unknown choice: $choice" >&2; exit 1 ;;
  esac
}

case "${1:-}" in
  claude) install_claude ;;
  codex)  install_codex ;;
  folder) install_folder "${2:-}" ;;
  ""|menu) choose ;;
  -h|--help) sed -n '2,12p' "$0" ;;
  *) echo "Unknown target: $1 (use claude|codex|folder, or no arg for menu)" >&2; exit 1 ;;
esac
