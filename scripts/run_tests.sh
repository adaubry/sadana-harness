#!/usr/bin/env bash
# ============================================================================
# Canonical test runner for sadana-harness. One file, one door.
#
#   scripts/run_tests.sh                          whole suite
#   scripts/run_tests.sh tests/config             one directory
#   scripts/run_tests.sh tests/config/test_x.py   one file
#   scripts/run_tests.sh -k resolver              any pytest flag passes through
#
# HEALTHY OUTPUT — paste this into CLAUDE.md § Verifying your work:
#
#     ▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
#     ......                                                   [100%]
#     6 passed in 0.41s
#     TESTS OK
#
# The last line is the contract. `TESTS OK` and exit 0 mean the suite ran and
# passed. Anything else is a failure, including a run that collected nothing.
#
# What it enforces:
#   · WSL only — a Windows interpreter is refused, matching the agent hook
#   · a venv whose python can IMPORT pytest, not merely exist
#   · env -i — empty environment, explicit allowlist, so no credential can
#     reach a test without appearing in a reviewable diff
#   · TZ=UTC, C.UTF-8, PYTHONHASHSEED=0 — deterministic, CI-identical
#   · zero collected is a FAILURE, never a pass
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

case "$(command -v python || true)" in
  /mnt/c/*)
    echo "error: python resolves to a Windows interpreter ($(command -v python))." >&2
    echo "       sadana is WSL-only. Activate the WSL venv and retry." >&2
    exit 1 ;;
esac

# The green-zero trap: a venv selected by existence alone may have no pytest.
# Every file then dies with "No module named pytest" and the run reports zero
# passed — which reads green at a glance. A candidate must IMPORT pytest.
PYTHON=""; SKIPPED=""
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "${VIRTUAL_ENV:-}"; do
  [ -n "$candidate" ] || continue
  [ -x "$candidate/bin/python" ] || continue
  if "$candidate/bin/python" -c 'import pytest' 2>/dev/null; then
    PYTHON="$candidate/bin/python"; break
  fi
  SKIPPED="$SKIPPED $candidate"
done
if [ -z "$PYTHON" ]; then
  echo "error: no virtualenv with pytest found." >&2
  echo "       looked in: $REPO_ROOT/.venv, $REPO_ROOT/venv, \$VIRTUAL_ENV" >&2
  [ -n "$SKIPPED" ] && echo "       skipped (no pytest installed):$SKIPPED" >&2
  echo "TESTS FAILED — no usable interpreter" >&2
  exit 1
fi

cd "$REPO_ROOT" || exit 1
echo "▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env"

env -i \
  PATH="$PATH" \
  HOME="$HOME" \
  TZ=UTC \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  PYTHONHASHSEED=0 \
  PYTHONUTF8=1 \
  "$PYTHON" -m pytest "$@"
rc=$?

# pytest exit codes: 0 passed · 1 failures · 2 interrupted · 3 internal
#                    4 usage error · 5 NO TESTS COLLECTED
case "$rc" in
  0) echo "TESTS OK" ;;
  5) echo "TESTS FAILED — no tests were collected." >&2
     echo "  An empty suite is a red suite, not a green one. If you meant to" >&2
     echo "  narrow the run, check the path; if the suite is genuinely empty," >&2
     echo "  that is the thing to fix." >&2 ;;
  *) echo "TESTS FAILED (pytest exit $rc)" >&2 ;;
esac
exit $rc
