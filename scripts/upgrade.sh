#!/usr/bin/env bash
# Runs detached from `door/nouns/harness.py`'s own `upgrade` action, after
# that action has already written the operation row `running` with the
# target tag recorded. This process outlives the one that spawned it — the
# `systemctl restart` below kills the parent along with everything else —
# and `door/operations.resume_on_start` (H30) is what resolves the
# operation afterward, on the *new* process's own boot, by comparing
# `sadana.__version__` against the tag this script checked out.
#
# The caller has already validated `$1` with
# `plugin_install.is_valid_tag_syntax` before ever invoking this script.
# `refs/tags/` below is still a real, structural defense of its own: a
# fixed, non-attacker-controlled prefix means the argument git actually
# sees can never start with `-`, regardless of what `$1` contains
# (CLAUDE.md's own rule on a caller-supplied git argument).
set -euo pipefail

tag="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$repo_root"
git fetch --tags
git checkout "refs/tags/$tag"
"$repo_root/.venv/bin/pip" install -e . --quiet
sudo systemctl restart sadana-gateway
