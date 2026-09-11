#!/usr/bin/env python3
"""Standalone proof that the `web-search` plugin really reaches Brave Search
and comes back with results.

Not a pytest test — testing-conventions bars the network from the unit
suite, and CLAUDE.md requires a block's first real external round trip to be
proved by a standalone script, its output pasted into review.md's
## Evidence, rather than relaxing the unit suite's network ban.

What it proves that the unit suite cannot: that the URL this project builds
is one Brave accepts, that a key placed by `sadana plugin set` is found by
`plugins.read_setting` under the name this project computes, that the reply's
shape is the one `web_search.parse` expects, and that the whole thing survives
`run_graph`'s own walk including the approval gate.

It runs the plugin exactly as a conversation would — the real `plugin.toml`,
the real graph, the real HTTP — with only the approval prompt auto-accepted,
since there is nobody at a keyboard.

Usage:
    python3 scripts/prove_web_search.py ["some query"]

Needs a key. Either:
    sadana plugin set web-search api_key          (the real path)
    SADANA_PLUGIN__WEB_SEARCH__API_KEY=... python3 scripts/prove_web_search.py

Free tier: https://brave.com/search/api/
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import config, plugins  # noqa: E402
from sadana.builtin_seed import SOURCE_ROOT  # noqa: E402
from sadana.plugin_manifest import run_graph, validate  # noqa: E402
from sadana.plugins import Valid  # noqa: E402

PLUGIN = "web-search"


async def _approve(plugin: str, node: str, _value: object) -> bool:
    print(f"  [approval] {plugin}.{node} — auto-accepted (no keyboard here)")
    return True


async def _ask(_skill: object, _text: str) -> str:
    raise AssertionError("web-search has no ask node")


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "sadana harness agent runtime"

    config.load_dotenv()
    var = plugins.setting_env_var(PLUGIN, "api_key")
    if plugins.read_setting(PLUGIN, "api_key") is None:
        print(f"No key. Set one with:  sadana plugin set {PLUGIN} api_key")
        print(f"(or export {var}=... for this run)")
        return 1
    print(f"key found via {var} (value not shown)")

    plugin_dir = SOURCE_ROOT / PLUGIN
    outcome = validate(plugin_dir)
    if not isinstance(outcome, Valid):
        print(f"the shipped plugin does not validate: {outcome}")
        return 1

    print(f"searching for {query!r} …\n")
    result = asyncio.run(
        run_graph(
            plugin_dir,
            outcome.manifest,
            outcome.manifest.entries[0],
            {"query": query, "count": 5},
            ask=_ask,
            approve=_approve,
        )
    )

    print(result.text)
    print()
    print(f"failed_node={result.failed_node!r}  trace={[t.node for t in result.trace]}")
    if result.failed_node is not None:
        return 1
    if "--- begin search results" not in result.text:
        print("\nReached the service but got no results block — see the text above.")
        return 1
    print("\nOK: a real round trip to Brave Search returned results through the plugin graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
