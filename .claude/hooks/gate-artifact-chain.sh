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
  # Heredoc/redirect into an artifact path is a write wearing a Bash coat.
  tgt=$(printf '%s' "$cmd" | grep -oE '(docs/tasks/[^[:space:]"'"'"']+|src/[^[:space:]"'"'"']+)' | head -1 || true)
  if [ -n "$tgt" ] && printf '%s' "$cmd" | grep -Eq '>|tee|sed -i|python .*write'; then
    python3 scripts/artifact.py gate write "$tgt" || exit $?
  fi
  if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+commit'; then
    python3 scripts/artifact.py gate commit - || exit $?
  fi
fi
exit 0
