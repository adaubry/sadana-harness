#!/usr/bin/env bash
# PreToolUse(Write|Edit|Bash) — enforce the six-step chain for the active
# work item.
#
# This hook decides nothing itself. It asks scripts/artifact.py, which is the
# same validator you run by hand and the same one CI runs. One definition of
# "the methodology was followed", three callers.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0
[ -f scripts/artifact.py ] || exit 0

input=$(cat)
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""')
cmd=$(printf  '%s' "$input" | jq -r '.tool_input.command // ""')

if [ -n "$path" ]; then
  rel="${path#"$PWD/"}"
  python3 scripts/artifact.py gate write "$rel" || exit $?
  exit 0
fi

if [ -n "$cmd" ]; then
  # A heredoc, redirect, tee, sed -i or python open(...,'w') aimed at a gated
  # path is a write wearing a Bash coat. The path has to be the actual
  # DESTINATION, not merely present in the command, or a read-only
  # `grep foo src/x.py > /tmp/out` gets gated as a write to src/x.py.
  pathre='(docs/tasks/[^[:space:]"'"'"']+|src/[^[:space:]"'"'"']+|tests/[^[:space:]"'"'"']+)'
  openre="open\\((\"|')$pathre(\"|')[[:space:]]*,[[:space:]]*(\"|')[aw]"
  # EVERY destination, not just the last one. `tail -1` here used to mean two
  # heredocs in one command were gated by whichever came second, so putting a
  # plan-listed path last waved every unlisted file before it straight through.
  targets=$(printf '%s' "$cmd" \
    | grep -oE ">>?[[:space:]]*$pathre|tee[[:space:]]+(-a[[:space:]]+)?$pathre|sed[[:space:]]+-i[^[:space:]]*[[:space:]].*$pathre|$openre" \
    | grep -oE "$pathre" | sort -u || true)
  # A here-string keeps the loop in this shell, so `exit` below really exits.
  while IFS= read -r tgt; do
    [ -n "$tgt" ] || continue
    python3 scripts/artifact.py gate write "$tgt" || exit $?
  done <<<"$targets"

  if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+commit'; then
    python3 scripts/artifact.py gate commit - || exit $?
  fi
fi
exit 0
