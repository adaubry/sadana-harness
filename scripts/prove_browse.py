#!/usr/bin/env python3
"""Standalone proof that the `browse` plugin really drives a real browser.

Not a pytest test — testing-conventions bars the network from the unit suite,
and CLAUDE.md requires a block's first real external round trip to be proved by
a standalone script, its output pasted into review.md's ## Evidence.

What it proves that the unit suite cannot: that `AGENT_SCRIPT` — a string
constant nothing type-checks or lints — actually runs; that `uvx` resolves
browser-use; that a browser is present and drivable; that `ChatOpenRouter`
accepts the key this project stores; and that what comes back is a report
rather than a crash.

It runs the plugin exactly as a conversation would: the real `plugin.toml`, the
real graph, the real program, with only the approval prompt auto-accepted.

**Each run costs model calls** — browsing is many steps and every one is an
inference. It also opens a real browser on this machine.

Usage:
    python3 scripts/prove_browse.py ["a task in plain English"]

Needs a key:
    sadana plugin set browse api_key
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import browse, config, plugins  # noqa: E402
from sadana.builtin_seed import SOURCE_ROOT  # noqa: E402
from sadana.plugin_manifest import run_graph, validate  # noqa: E402
from sadana.plugins import Valid  # noqa: E402

PLUGIN = "browse"


async def _approve(plugin: str, node: str, _value: object) -> bool:
    print(f"  [approval] {plugin}.{node} — auto-accepted (no keyboard here)")
    return True


async def _ask(_skill: object, _text: str) -> str:
    raise AssertionError("browse has no ask node")


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "Go to example.com and report the exact heading on the page."

    config.load_dotenv()
    var = plugins.setting_env_var(PLUGIN, "api_key")
    if plugins.read_setting(PLUGIN, "api_key") is None:
        print(f"No key. Set one with:  sadana plugin set {PLUGIN} api_key")
        print(f"(or export {var}=... for this run)")
        return 1
    print(f"key found via {var} (value not shown)")
    print(f"model: {browse.model()}   timeout: {browse.timeout_s()}s")

    plugin_dir = SOURCE_ROOT / PLUGIN
    outcome = validate(plugin_dir)
    if not isinstance(outcome, Valid):
        print(f"the shipped plugin does not validate: {outcome}")
        return 1

    print(f"task: {task!r}")
    print("running a real browser — this costs model calls and opens a window\n")

    result = asyncio.run(
        run_graph(
            plugin_dir,
            outcome.manifest,
            outcome.manifest.entries[0],
            {"task": task},
            ask=_ask,
            approve=_approve,
        )
    )

    print("======== what the browser reported ========")
    print(result.text)
    print("======== end of report ========")
    print(f"\nfailed_node={result.failed_node!r}  trace={[t.node for t in result.trace]}")

    if result.failed_node is not None:
        return 1
    # `startswith`, not `in`: both sentences this plugin can produce for a
    # failure begin that way, while a page legitimately reporting "could not
    # find the price" is a *successful* browse and must not fail the proof.
    if result.text.startswith("The browser could not"):
        print("\nThe browser did not complete the task — see above.")
        return 1
    print("\nOK: a real browser carried out the task and reported back through the plugin graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
