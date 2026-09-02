#!/usr/bin/env bash
# Stop hook — the tree must be green before a session ends.
#
# The loop guard matters: exit 2 tells Claude Code to keep going and feeds
# stderr back. Without checking stop_hook_active, a permanently-red tree would
# trap the session forever. We block exactly once, then let it stop and report.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

input=$(cat)
if [ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0
fi

if out=$(make verify 2>&1); then
  exit 0
fi

{
  echo "make verify is not green — do not report this task complete."
  echo "$out" | tail -30
} >&2
exit 2
