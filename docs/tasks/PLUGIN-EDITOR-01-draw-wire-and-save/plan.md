# Plan: Building a plugin by drawing it (from intent.md 2026-09-10)

## Context

PLUGIN-EDITOR-01 is the first Phase 2 work item: a browser page where someone
who cannot write code lays out a plugin's steps as boxes joined by arrows,
saves it, and reopens it. What it writes is the same `plugin.toml` a
programmer writes by hand — which is only possible because the graph was kept
as declared data (D2/D3), and is exactly why hermes has no such tool anywhere
in its 2,368 files.

`intent.md` and `spec.md` are written, validated and approved; spec.md already
names every file, argues every declined alternative (FastAPI/uvicorn,
Vite/React, `tomli-w`, storing box positions, serving from the gateway
daemon), and resolves the draft-vs-installable tension via
`validate(check_bodies=False)`. This plan orders the work and names the tests;
it does not re-decide the design.

## Files that change

- `src/sadana/plugins.py` — two new pure functions beside their existing
  inverses: `manifest_to_toml(manifest) -> str` (the **first code in this
  project that writes a plugin file**; escaping delegated to
  `json.dumps(s, ensure_ascii=False)`, where `ensure_ascii=False` is
  load-bearing — the default emits non-BMP characters as surrogate pairs,
  which TOML rejects) and `manifest_from_dict(data) -> Manifest` (inverse of
  the existing `manifest_to_dict`, raising `ValueError` naming the bad field,
  never a bare `KeyError`). Strictly additive: `_parse_manifest` and
  `manifest_to_dict` have live callers (`plugin_manifest.validate`,
  `marketplace.py:265`) and are not touched.
  `tests/unit/test_plugins.py` — round trip over all four real fixtures
  (`plugin-a`..`plugin-d`, covering call/ask/route/stop/wait/compute), a
  hostile-string case (quote, backslash, newline, tab, emoji), and
  `manifest_from_dict`'s three named `ValueError` cases.
- `src/sadana/editor_layout.py` (new, pure) — `positions(manifest) ->
  dict[str, tuple[int, int]]`, a layered walk from each entry's `start`:
  depth sets the column, order within a depth sets the row; defined for
  unreachable nodes too. Deterministic, which is what makes reopening show
  the same picture without storing anything.
  `tests/unit/test_editor_layout.py` (new).
- `src/sadana/editor_server.py` (new, I/O: disk + sockets) — one
  socket-free entry point, `handle(method, path, body, *, plugins_root) ->
  Response(status, body, content_type)`, holding all routing, name checking,
  reading and writing; plus a `make_server()` whose handler class only reads
  `Content-Length` bytes, calls `handle`, and writes the result. Same
  pure-core/thin-shell split `channel_webhook.parse_webhook_request` already
  uses, taken one step further so the whole API surface is testable with no
  socket. Lists plugin directories directly rather than via
  `discover_plugins()`, which by design returns only plugins that fully
  validate and would hide exactly the unfinished ones the editor exists to
  edit.
  `tests/unit/test_editor_server.py` (new).
  **Two amendments during build**, both from running the thing: a sixth
  route, `GET /api/kinds`, serves the palette from the declared `NodeKind`
  (requirement 10 needed it and the spec's first draft named only four
  endpoints); and `needs_code` became `waiting`, covering an `ask` step's
  missing skill file as well as a missing body, because reporting one as a
  to-do and the other as a validation problem told a person two stories about
  one situation. spec.md amended in the same diff.
- `src/sadana/editor_assets/` (new) — `index.html`, `editor.js`, `editor.css`.
  Inline SVG canvas, no framework, no build step. The only part `make verify`
  cannot check; lands last, after the API it talks to is frozen.
- `src/sadana/subcommands/editor.py` (new) — `sadana editor [--port]`,
  mirroring `subcommands/marketplace.py:155-171`'s
  `make_server` + `gateway_daemon.run(..., lock_filename="editor.lock")`
  shape. Refuses any non-loopback host with no override flag.
  `tests/unit/test_subcommands_editor.py` (new).
- `src/sadana/cli.py` — one import and one `build_editor_parser(subparsers)`
  call. `test_cli.py` pins no exact subcommand list (checked), so it needs no
  change.
- `scripts/prove_editor_e2e.py` (new) — real server, real HTTP.
- `CLAUDE.md` — already modified in the working tree during design (the
  browser-holds-no-logic rule and the caller-supplied-name path rule, both
  approved). Part of this work item's commit; not touched again by this plan.

## Order of work

1. `plugins.py`'s two functions + their tests. Pure, additive, and everything
   downstream needs them. Narrow: `bash scripts/run_tests.sh
   tests/unit/test_plugins.py`, `make typecheck`.
2. `editor_layout.py` + tests. Pure, independent of everything above.
3. `editor_server.py`'s `handle()` + tests — the largest chunk of logic:
   routing, the name allowlist plus post-resolution root check, GET list,
   GET one (manifest + positions + problems + needs_code), PUT, POST, and the
   400/404/409 shapes. Socket-free, so all of it is unit-tested.
4. `editor_server.py`'s `make_server()` — the thin socket shell. Not
   unit-tested, matching `channel_webhook.py`'s own precedent; proven by
   step 7.
5. `subcommands/editor.py` + `cli.py` wiring + tests (loopback refusal,
   parser wiring, `gateway_daemon.run` monkeypatched — the shape
   `test_subcommands_gateway.py` already uses).
6. `editor_assets/` — the page. Last, deliberately: see Risks.
7. `scripts/prove_editor_e2e.py`, run for real: create a plugin, read it back,
   change a step, save, reload, and confirm the file on disk parses to what
   the browser last saw.
8. Self-check (`/ponytail-review` + `/simplify`), then `make verify`.
   **What it actually found**, all fixed in this diff with regression tests:
   two ways to destroy a working plugin on save (a lone surrogate truncating
   the file, U+007F writing an unreopenable one) plus the structural fix that
   makes both classes impossible — emit, parse back, then write; a
   cross-origin hole (loopback binding is not access control against a
   browser); a 500 leaking the plugins directory's absolute path; a plugin's
   name being allowed to diverge from its folder; and the graph rewrites
   (add/rename/delete) moving out of untestable JavaScript into pure Python
   behind a new `POST /api/plugins/<name>/steps`. spec.md amended to match.

## Risks

**What could this break?** Named, and two were checked before writing this:

- `plugins.py` is imported by eight modules. The change is two new
  module-level functions; nothing existing is renamed or altered. Re-grep
  `manifest_to_dict`/`_parse_manifest` call sites before editing to confirm
  the additive claim holds.
- `cli.py` gaining a subcommand — **checked**: `test_cli.py` asserts only
  "unrecognised command exits 2" and per-subcommand reachability, never an
  exact list, so nothing there breaks.
- Tests writing into a developer's real plugins directory — **checked**:
  `conftest._isolated_state` redirects `SADANA_STATE_DIR`, and
  `plugins._plugins_root()` defaults beneath it. Belt and braces:
  `handle()` takes `plugins_root` as an explicit parameter, so its tests
  never consult the environment at all.
- `gateway_daemon.run` gains a third caller but is not modified.

**Which step is riskiest, and why that one?** Step 6, the assets — not because
it is hard, but because it is the only step no gate in this project can check.
`make verify` will report green whatever state the page is in. It lands last
so the API it talks to is already frozen and fully tested, which reduces it to
purely visual/interaction risk rather than contract risk. **I should say
plainly now, so it is not a surprise at review: I have no browser here, so I
can prove the server, the round trip and the page being served, but I cannot
verify that a box actually drags.** The completion claim will be scoped to
exactly that, and the reviewer should read the JavaScript knowing nothing else
will.

**Drift check against spec.md's Rejected alternatives.** Re-read before
writing this plan. Not drifting back toward: FastAPI/uvicorn or a Vite/React
build (stdlib server, hand-written page); `tomli-w` (hand-rolled emitter with
a round-trip test); gating saves on `validate()` (it reports, never blocks);
serving from the gateway daemon (its own command and lock file); storing box
positions (computed every time). The one live temptation is the last: when the
page adds a node it would be convenient to place it in JavaScript. It must
not compute the layout — the server stays authoritative, and the page places a
new box at a fixed offset until the next server answer redraws it. That is the
browser-holds-no-logic rule this work item just added to CLAUDE.md.

## Proof

- `manifest_to_toml` → `tomllib.loads` → `_parse_manifest` returns a
  `Manifest` equal to the original, for each of the four real fixture plugins
  and for a manifest whose description contains a quote, a backslash, a
  newline, a tab and an emoji. That last case is what would catch the
  `ensure_ascii` mistake.
- `manifest_from_dict(manifest_to_dict(m)) == m` for the same fixtures, plus
  `ValueError` naming the field for a missing `name`, a missing `kind`, and a
  non-list `ports`.
- `positions()` returns an entry for every declared node including an
  unreachable one, twice in a row identically, with an entry's `start` left
  of its successors.
- A saved plugin whose node declares a body no file supplies reloads, and its
  `needs_code` names that node — the requirement that `validate()` alone would
  have refused.
- Names containing `..`, `/`, or an absolute path are rejected before any
  filesystem access, asserted by a test that no file outside the given root is
  created or read.
- A non-loopback host is refused by `sadana editor`.
- `scripts/prove_editor_e2e.py`'s real output, pasted into `review.md`'s
  `## Evidence`.
- `make verify` ending `VERIFY OK`, pasted in full.
