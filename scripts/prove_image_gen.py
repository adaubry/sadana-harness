#!/usr/bin/env python3
"""Standalone proof that the `image-gen` plugin really draws a picture and
really writes it to disk.

Not a pytest test — testing-conventions bars the network from the unit suite,
and CLAUDE.md requires a block's first real external round trip to be proved
by a standalone script, its output pasted into review.md's ## Evidence.

**This one has a second job.** For `web-search` the unproven half was the
round trip. Here it is making a file: nothing in this project had ever written
one before `ARTIFACT-STORE-01` built somewhere to put it, and nothing had used
that until now. So this script does not stop at "the request succeeded" — it
reports the file's path, its size, and its leading magic bytes, because the
claim being proved is that a picture exists on disk.

It runs the plugin exactly as a conversation would: the real `plugin.toml`,
the real graph, the real HTTP, the real artifact directory. Only the approval
prompt is auto-accepted, since there is nobody at a keyboard.

**Each run costs money.** It bills the account behind the key.

Usage:
    python3 scripts/prove_image_gen.py ["a description"]

Needs a key:
    sadana plugin set image-gen api_key
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import artifact_store, config, image_gen, plugins  # noqa: E402
from sadana.builtin_seed import SOURCE_ROOT  # noqa: E402
from sadana.plugin_manifest import run_graph, validate  # noqa: E402
from sadana.plugins import Valid  # noqa: E402

PLUGIN = "image-gen"

# What each format's first bytes look like, so the script can say "this is
# really a picture" rather than "a file exists".
_MAGIC = {b"\x89PNG\r\n\x1a\n": "PNG", b"\xff\xd8\xff": "JPEG", b"RIFF": "WEBP", b"GIF8": "GIF"}


async def _approve(plugin: str, node: str, _value: object) -> bool:
    print(f"  [approval] {plugin}.{node} — auto-accepted (no keyboard here)")
    return True


async def _ask(_skill: object, _text: str) -> str:
    raise AssertionError("image-gen has no ask node")


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "a single red bicycle against a plain white wall"

    config.load_dotenv()
    var = plugins.setting_env_var(PLUGIN, "api_key")
    if plugins.read_setting(PLUGIN, "api_key") is None:
        print(f"No key. Set one with:  sadana plugin set {PLUGIN} api_key")
        print(f"(or export {var}=... for this run)")
        return 1
    print(f"key found via {var} (value not shown)")
    print(f"model: {image_gen.model()}")

    plugin_dir = SOURCE_ROOT / PLUGIN
    outcome = validate(plugin_dir)
    if not isinstance(outcome, Valid):
        print(f"the shipped plugin does not validate: {outcome}")
        return 1

    out = artifact_store.for_run("prove-image-gen", 0, 0)
    print(f"output directory: {out}")
    print(f"drawing {prompt!r} … (this bills the account behind the key)\n")

    result = asyncio.run(
        run_graph(
            plugin_dir,
            outcome.manifest,
            outcome.manifest.entries[0],
            {"prompt": prompt},
            ask=_ask,
            approve=_approve,
            output_dir=out,
        )
    )

    print(f"\nresult text : {result.text}")
    print(f"failed_node : {result.failed_node!r}   trace={[t.node for t in result.trace]}")
    if result.failed_node is not None or not result.artifacts:
        print("\nNo picture was produced — see the text above.")
        return 1

    artifact = result.artifacts[0]
    path = Path(artifact.ref)
    if not path.is_file():
        print(f"\nThe run reported {path}, and there is no file there.")
        return 1

    data = path.read_bytes()
    kind = next((name for magic, name in _MAGIC.items() if data.startswith(magic)), None)
    print(f"\nfile        : {path}")
    print(f"size        : {len(data):,} bytes")
    print(f"first bytes : {data[:8]!r}")
    print(f"looks like  : {kind or 'UNRECOGNISED — not an image this script knows'}")

    if kind is None:
        print("\nA file was written but it is not a picture.")
        return 1
    print(f"\nOK: a real {kind} was drawn by a real service and written to disk through the plugin graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
