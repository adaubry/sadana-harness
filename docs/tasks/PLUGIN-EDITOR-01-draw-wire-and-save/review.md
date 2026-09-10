# Review: Building a plugin by drawing it (from plan.md 2026-09-10)

Reviewed: HEAD (working tree, uncommitted — nothing is committed for this work
item yet) — 18 files, +2953/-11
Reviewer context: fresh session — delegated to a freshly-spawned subagent with
no context beyond the diff and the three artifacts, per this stage's own "buy
the separation instead of assuming it." It read the real files, ran the test
suite and the e2e script itself, and demonstrated each finding against the
running code rather than reasoning about it.
Second opinion: the build stage already ran its own self-check (a
`/ponytail-review` pass plus two parallel review agents, one for
simplification/reuse and one for correctness/security). That pass found and
fixed real defects — a lone surrogate truncating a plugin file, U+007F writing
an unreopenable one, a cross-origin form reaching the server, a 500 leaking
absolute paths — all before this review began. This review is not a repeat of
it: it found eight further Important items, five of them in code the
self-check had already touched.

## Evidence

```
$ make verify
docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 40 source files(B
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 10%]
........................................................................ [ 21%]
........................................................................ [ 32%]
........................................................................ [ 42%]
........................................................................ [ 53%]
........................................................................ [ 64%]
........................................................................ [ 74%]
........................................................................ [ 85%]
........................................................................ [ 96%]
.........................                                                [100%]
673 passed in 16.35s
TESTS OK
VERIFY OK
```

`scripts/prove_editor_e2e.py` ends `ALL ASSERTIONS PASSED` on a real bound
socket — run by this session and re-run independently by the cold reviewer.
Note what it does *not* cover: see the drag finding below.

## Scope check

Seventeen of the diff's eighteen files are named in `plan.md`'s `## Files that
change`; nothing plan.md named is missing. The eighteenth is a finding below.

## Findings

**Important**

- **[Security]** `body`, `skill` and `parameters` arrive from the browser and
  become filesystem paths with no validation at all — the very rule this diff
  adds to CLAUDE.md, applied to one of the four caller-supplied path-forming
  strings and not the other three. `editor_server.py:108` builds
  `directory / f"{module}.py"` straight from `node.body`'s module half and
  `:112` builds `directory / "skills" / node.skill / "SKILL.md"`, neither
  going through `_plugin_dir`'s allowlist-then-containment treatment. Today
  that is already an arbitrary-file existence oracle and, through
  `_defined_functions`, an enumeration of any `.py` file's top-level function
  names. The escalation is worse and was demonstrated end to end: a single
  `PUT /api/plugins/<name>` with `body = "../../elsewhere/module:function"`
  is persisted to `plugin.toml`, and the *next* `discover_plugins()` — the
  agent runtime, not the editor — reaches `plugin_manifest._load_body_module`,
  which does `spec.loader.exec_module(module)` on a file anywhere on the
  filesystem. The editor itself still executes nothing, so requirement 8
  holds and this passes every test in the diff; that is exactly why it is
  easy to miss. The fix belongs where `_plugin_dir` already sits: refuse a
  `body`/`skill`/`parameters` that resolves outside the plugin's own
  directory, in `manifest_from_dict` or `_save`.
  **Fixed.** `_outside_the_plugin` (`editor_server.py`) now checks all three
  at `_write_manifest`, the one chokepoint every write passes through: a
  `body` must be `module:function` with both halves plain Python identifiers,
  a `skill` must be one dot-free path segment, and a `parameters` path must
  be relative, `..`-free and still inside the plugin after resolution. The
  reviewer's own end-to-end exploit was re-run against the fix: the `PUT` is
  refused 400, nothing is written to `plugin.toml`, and a subsequent
  `discover_plugins()` with bodies checked executes nothing. Regression tests
  cover all three fields, including one asserting the ordinary shapes every
  real plugin uses (`init:fetch`, a dashed skill name, `schema/x.json`) still
  save.

- **[Bugs]** Dragging a box does not work at all, and every click on one
  throws. `editor.js:174` calls `draw()`, which does `boxes.replaceChildren()`
  and so detaches `event.currentTarget` from the document; `:180` then calls
  `setPointerCapture` on that detached element, which the Pointer Events spec
  requires to throw `InvalidStateError` for an element not in its document's
  tree. The handler aborts there, so the `pointermove`/`pointerup` listeners
  are never attached. Selection appears to work (it happened before the
  throw), so the failure reads as "clicking works, dragging silently does
  not," with a console exception per click. Even with capture succeeding,
  `onMove` calls `draw()` again and re-detaches, so a box would follow the
  pointer for exactly one event. This is the interaction `intent.md` leads
  with, and it is the one thing plan.md's Risks section predicted no gate
  here could catch — correctly.
  **Fixed.** The move and up listeners now go on `window` and there is no
  `setPointerCapture` at all, so a redraw replacing the box cannot detach what
  the drag depends on. This remains the one change in the diff that nothing in
  this repository can verify: `node --check` (available on this machine, not
  wired into `make verify` — spec.md declined a Node toolchain) confirms the
  file parses, and the reasoning follows the Pointer Events spec the reviewer
  cited, but no test here proves a box moves.

- **[Bugs]** Deleting the last step permanently bricks a plugin, and only
  someone who can hand-edit TOML can rescue it — which is precisely the
  person this work item exists because they do not exist.
  `plugins.py:645` (`remove_node`) sets `fallback = kept[0].name if kept
  else ""`, so removing the final node leaves every entry with `start = ""`.
  `add_node` does not repair it and spec.md's Non-goals rule out any entry
  editing, so the plugin sits at `"has problems"` — `node 'x_entry' targets
  undeclared node ''` — forever. Cheapest fix: `add_node` re-points any entry
  whose `start` names no existing node.

- **[Security]** `_from_this_machine`'s `Origin` check accepts any loopback
  origin rather than this server's own, contradicting its own docstring.
  `editor_server.py:383-384` reduces the Origin to a bare hostname and
  compares against `("localhost", "127.0.0.1", "::1")`, discarding scheme and
  port, so `Origin: http://localhost:3000` and `Origin: https://127.0.0.1`
  both pass. Any page served from any other port on the same machine — a dev
  server, a notebook, the marketplace daemon, another sadana surface — is
  therefore a same-origin peer with read/write access to the user's plugins.
  The docstring says "an `Origin`, when the browser sends one, must be this
  same server," and requirement 9's framing is that this check is what a
  hosted version swaps for a real session check; it needs to compare the full
  origin against the address actually bound.

- **[Bugs]** A hand-written plugin whose directory name is not
  `[a-z0-9][a-z0-9-]{0,63}` is listed in the sidebar and then refused when
  opened, with no message at all. `_list_plugins` (`editor_server.py:180-188`)
  accepts any directory holding a `plugin.toml`, while `/api/plugins/<name>`
  runs the same name through `_SAFE_NAME` and answers 400. Underscores,
  capitals and dots are ordinary directory names for a hand-written plugin —
  nothing else in this project constrains them, since `discover_plugins` keys
  identity off the manifest rather than the folder. Because `editor.js:57`'s
  click handler has no rejection handler, the sidebar button simply does
  nothing. That is requirement 4's "what a programmer wrote by hand opens and
  draws correctly" failing on a plausible input. A symlinked plugin directory
  fails the same way.

- **[Bugs]** `::1` is accepted as a bind host, cannot actually bind, and would
  refuse every request if it did — and a test asserts it works. Three
  breakages on one path: `subcommands/editor.py:34-38` accepts it via
  `ipaddress.is_loopback`; `http.server.HTTPServer.address_family` is
  `AF_INET`, so `make_server("::1", ...)` raises `gaierror` *after*
  `gateway_daemon.run` has taken the lock; and `_from_this_machine`
  mis-parses the bracketed IPv6 `Host` a browser sends —
  `"[::1]:8770".split(":")[0].strip("[]")` is `""` — so every request would be
  403. `subcommands/editor.py:60` also prints the invalid URL
  `http://::1:8770/`. `test_editor_serves_the_loopback_addresses[::1]` passes
  only because `gateway_daemon.run` is monkeypatched, so the suite currently
  asserts a capability that does not exist.

- **[Bugs]** `_write_manifest`'s save is not atomic, so the round-trip guard
  does not cover every way a save can destroy a plugin.
  `editor_server.py:242` is a plain `write_text` — truncate, then write. The
  emit-parse-then-write structure genuinely does turn *emitter* bugs into
  refusals (the reviewer could not defeat that part), but spec.md's Interface
  states the hardening as the unconditional "A save never truncates a working
  plugin," and an `ENOSPC`, a quota, or a process kill between truncate and
  flush still leaves a zero-length or half-written `plugin.toml` with no
  backup. The emitted text is already validated and in hand, so writing to a
  sibling temp file and `os.replace` is a two-line change that makes the
  stated promise true.

- **[Compliance]** `src/sadana/plugin_manifest.py:384-385` is changed and
  `plan.md`'s `## Files that change` never names it. The edit swaps
  `if node.kind == "each"` for `if node.kind in plugins.KINDS_NOT_RUNNABLE` in
  `run_graph`, made during the build's self-check to remove a matched pair.
  It is behaviour-preserving — the f-string reproduces the old message
  byte-for-byte and the set holds only `"each"` — so this is paperwork rather
  than a defect, but it is a change to the plugin *runtime's* execution path
  under a plan whose spec says "every other module is untouched."

**Nits**

- **[Bugs]** `editor.js:115`/`:119` dereference `kindInfo(step.kind).runnable`
  unguarded, and nothing validates `kind` on the way in from disk —
  `_parse_manifest` does not and `validate()`'s eight checks do not — so a
  hand-written `kind = "computer"` makes `draw()` throw partway and leaves a
  blank canvas with no explanation. `manifest_from_dict` guards the browser
  door correctly; the file-on-disk door is unguarded.
- **[Security]** `problems` echoes absolute filesystem paths to the browser:
  `describe_manifest_outcome`'s `InvalidSchema` branch formats `str(path)`, so
  an ordinary missing schema file puts the plugins directory's absolute path
  in the UI — the same class of leak the self-check deliberately closed for
  500s, reopened through a 200.
- **[Bugs]** `editor.js:57`'s `openPlugin` call and `:271`'s `start()` have no
  rejection handler, so any failure is an unhandled promise rejection and a
  page that simply sits there. `save()` and `stepOp()` both report through
  `$("saved-note")`; these two do not.
- **[Bugs]** Any query string 404s — `handle` never splits it off the path, so
  `GET /assets/editor.css?v=2` returns "no such file." Harmless for the page's
  own fetches, and it forecloses cache-busting.
- **[Bugs]** `_create` writes the schema file before the manifest
  (`editor_server.py:250-256`), so a failure between them would leave a
  directory with no `plugin.toml`: hidden by `_list_plugins`, and answered 409
  by `_create` for that name forever. Unreachable today, but the ordering is
  backwards for no reason.

**What the reviewer checked and found clean**

- **The TOML writer — no third defect.** Round-tripped `manifest_to_toml` →
  `tomllib` → `_parse_manifest` over every code point `0x0000`–`0x10FFF`
  excluding surrogates, a sweep of the astral planes to `0x10FFFF`, and
  ~28,000 random strings seeded with `\x00`, `\x7f`, `\x1b`, `\r`,
  ` `/` `, BOM, combining marks, `"""`, `'''`, backslashes and
  astral emoji, applied to every string field individually. Zero mismatches,
  zero spurious rejections. `TOMLDecodeError` and `UnicodeEncodeError` are
  both `ValueError` subclasses, so `_write_manifest`'s contract holds, and
  `manifest_to_toml` has exactly one caller, so the round-trip guard cannot
  be bypassed.
- **`_plugin_dir` and `_asset`.** Allowlist plus post-resolution containment
  is correct; the path is never URL-decoded, so `..%2Fescape.js` stays
  literal and the regex rejects it.
- **`_from_this_machine`, apart from the Origin looseness above.** Refused: a
  foreign `Host`, `127.0.0.1.evil`, `localhost.evil`, `127.0.0.2`,
  decimal/octal `Host` spellings, a missing `Host` (fails closed), and
  `Origin: null`. The `enctype="text/plain"` cross-origin form the build's
  self-check demonstrated is genuinely closed, since Fetch mandates `Origin`
  on every non-GET/HEAD request and there is no state-changing GET route.
- **Requirement 8 holds on every path** — the editor executes no plugin code,
  re-confirmed independently against `ast.parse`, `check_bodies=False`, and
  the filesystem-sentinel test.
- **Every acceptance criterion in spec.md is discharged**, each named to a
  specific test; the e2e script was re-run on a real socket.
- **No drift toward any rejected alternative** — no FastAPI/uvicorn, no
  bundler or `package.json`, `pyproject.toml` untouched so no `tomli-w`,
  saves gate on nothing, the editor has its own command and lock file, and
  layout is computed only in `editor_layout.py`. The page does not even place
  newly-added boxes locally; it round-trips through `POST /steps`, which is
  stricter than plan.md promised.

**Raised, not findings**

- spec.md's Concern 5 was independently confirmed: `[tool.setuptools.packages.find]`
  has no `package-data` and `editor_assets/` has no `__init__.py`, so a built
  wheel would ship none of the three asset files and `GET /` would 404. Already
  disclosed in the artifacts, and no consumer exists yet.

## Decision

Approved by Adam, 2026-09-10, with the two severe findings fixed in this
branch first — the path-traversal-to-code-execution hole and the drag
handler — each re-verified (the exploit re-run and refused; `make verify`
green at 673 tests; the e2e script re-run on a real socket).

The remaining six Important findings are knowingly carried forward, not
silently dropped: deleting the last step still bricks a plugin, the `Origin`
check still accepts any loopback origin rather than this server's own, `::1`
is still accepted as a bind host it cannot use (with a test asserting a
capability that does not exist), the save is still not atomic against
`ENOSPC` or a kill, a hand-written folder name outside
`[a-z0-9][a-z0-9-]{0,63}` still lists but refuses to open, and `plan.md`'s
file list still omits `plugin_manifest.py`. All five nits likewise. Whoever
picks this track up next should start there — the last-step brick and the
`Origin` looseness are the two that reach a person soonest.
