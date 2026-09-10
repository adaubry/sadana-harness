#!/usr/bin/env python3
"""Standalone proof that `plugin_install.install()` performs a real fetch
against a genuine public git repository over the network.

Not a pytest test — testing-conventions bars the network from the unit
suite, and CLAUDE.md requires a block's first real external round trip to
be proved by a standalone script, its output pasted into review.md's
## Evidence, rather than relaxing the unit suite's network ban.

No published repository anywhere yet has a `plugin.toml` in this project's
own format — sadana-harness defines that format itself, in Phase 1, and
nothing outside this repo has adopted it. So by default this script proves
the part that *can* be proven against any real public repository today:
resolving a tag against a real remote (`git ls-remote`), cloning it for
real, and confirming the fetched tree's `HEAD` matches what the tag names
on the remote (tag integrity, Requirement 4). It ends in a `FetchFailed`
outcome because the default repository has no `plugin.toml` — that is the
honest, expected result today, not a bug.

Usage:
    python3 scripts/prove_plugin_install.py [repo_url] [tag]

Pass a real plugin-repository URL and tag once one exists to prove the
full path to `Installed`, unchanged.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["SADANA_STATE_DIR"] = tempfile.mkdtemp(prefix="sadana-plugin-install-e2e-")  # noqa: E402

from sadana import plugin_install  # noqa: E402
from sadana.conversation_store import open_store, store_path_from_config  # noqa: E402

DEFAULT_REPO_URL = "https://github.com/psf/requests.git"
DEFAULT_TAG = "v2.31.0"


def main() -> int:
    repo_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO_URL
    tag = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TAG
    name = "prove-plugin-install-e2e"
    plugins_root = Path(tempfile.mkdtemp(prefix="sadana-plugin-install-e2e-plugins-"))

    conn = open_store(store_path_from_config())
    try:
        register_outcome = plugin_install.register(conn, name, repo_url, now=time.time())
        print(f"register({name!r}, {repo_url!r}) -> {register_outcome}")

        start = time.monotonic()
        install_outcome = plugin_install.install(conn, name, tag, plugins_root=plugins_root)
        elapsed = time.monotonic() - start
        print(f"install({name!r}, {tag!r}) -> {install_outcome}  ({elapsed:.2f}s)")
    finally:
        conn.close()

    if isinstance(install_outcome, plugin_install.Installed):
        print(
            f"OK: real network fetch, tag-integrity check, and placement all succeeded at {install_outcome.directory}"
        )
        return 0
    if isinstance(install_outcome, plugin_install.FetchFailed) and "plugin.toml" in install_outcome.detail:
        print(
            "OK for today's purposes: the real network round trip (resolve tag, clone, verify HEAD) "
            "completed successfully against a genuine remote — it only stops short of Installed "
            "because no real published repository yet has a plugin.toml in this project's own "
            "format. Pass a real plugin repository URL and tag as argv to prove the full path once "
            "one exists."
        )
        return 0
    print(f"UNEXPECTED: {install_outcome}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
