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
  # Heredoc/redirect into an artifact path is a write wearing a Bash coat —
  # but the path must be the actual destination of the redirect/tee/sed -i,
  # not just present anywhere else in the command. Without this, a read-only
  # `grep foo src/x.py > /tmp/out` gets gated as a write to src/x.py because
  # the command merely contains a `>` somewhere.
  pathre='(docs/tasks/[^[:space:]"'"'"']+|src/[^[:space:]"'"'"']+)'
  tgt=$(printf '%s' "$cmd" \
    | grep -oE ">>?[[:space:]]*$pathre|tee[[:space:]]+(-a[[:space:]]+)?$pathre|sed[[:space:]]+-i[^[:space:]]*[[:space:]].*$pathre" \
    | grep -oE "$pathre" | tail -1 || true)
  if [ -z "$tgt" ] && printf '%s' "$cmd" | grep -Eq 'python' && printf '%s' "$cmd" | grep -Eq '\.write\('; then
    tgt=$(printf '%s' "$cmd" | grep -oE "$pathre" | head -1 || true)
  fi
  if [ -n "$tgt" ]; then
    python3 scripts/artifact.py gate write "$tgt" || exit $?
  fi
  if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+commit'; then
    python3 scripts/artifact.py gate commit - || exit $?
  fi
fi
exit 0
