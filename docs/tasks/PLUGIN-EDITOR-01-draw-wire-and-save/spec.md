# Spec: Building a plugin by drawing it

Intent: docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/intent.md

## Requirements

1. A person can open a browser page, see the steps of an existing plugin as
   boxes joined by arrows, rearrange nothing and still recognise the plugin —
   intent's "reads it back to redraw."
2. A person can add a step, delete a step, rename a step, change which step
   an arrow leads to, and save — intent's "drags boxes and joins them with
   arrows."
3. A person can create a new plugin from nothing and save it, and what lands
   on disk is a plugin the rest of the system accepts without conversion —
   intent's Problem (someone with no way to express a procedure) and its
   "a plugin drawn in the editor runs with nothing converted in between."
4. What the editor writes is the same file a programmer writes by hand, and
   what a programmer wrote by hand opens and draws correctly — intent's "not
   a beginner's format that something else has to translate," in both
   directions.
5. All seven step kinds can be drawn and wired. A step that needs something
   nobody has written yet is drawn, wired, and visibly marked as waiting for
   it — intent's Proposed outcome, third paragraph.
   **Amended during build.** Both intent.md and this spec's first draft said
   three kinds (asks the agent, waits for an outside answer, ends the run)
   "need nothing from a programmer at all." Running the editor showed that is
   false for `ask`: it names a skill, and a skill is a `SKILL.md` somebody
   writes, which this editor gives no way to write. Only `wait` and `stop` are
   genuinely complete the moment they are drawn. See Design point 4.
6. The loop step appears in the palette marked as not yet usable — intent's
   Constraints.
7. Saving a plugin whose code parts are not filled in succeeds and reopening
   it works. It is not refused, and it is not saved into some separate draft
   place — intent's "a normal working state, not an error to refuse."
8. The editor never writes the code parts, and never executes them either —
   intent's "The editor never writes the code parts."
9. Refusing to serve anywhere but this machine is enforced, not documented —
   intent's local-for-now constraint, taken as the honest reading of "nothing
   may make a hosted version impossible later": see Design, guideline 1.
10. The palette contains exactly the step kinds the system declares and no
    mechanism exists to add an eighth without a code change — intent's
    "Adding a new kind is its own piece of work, never a setting."

## Design

**Policy conformance.** `testing-conventions` loaded and applied — see
Acceptance criteria, and the pure/I-O split below, which exists specifically
so the interesting logic is reachable by `make test` without a socket. No
skill named `project-structure` or `reference-lookup` exists in
`.claude/skills/` (the six present are audit, build, deploy, design, plan,
testing-conventions), so CLAUDE.md's own Layout and "The reference corpus"
sections are applied in their place, as in this project's other specs. No
security skill exists in the repo either; the security thinking is therefore
written out explicitly under "Names that become paths" below rather than
delegated, because this work item opens the first surface where a caller who
is not the machine's owner can send arbitrary strings.

**1. Learn from the reference before proposing.**

Read, in `../hermes-agent`: `hermes_cli/web_server.py` (20,070 lines),
`hermes_cli/subcommands/dashboard.py`, `hermes_cli/subcommands/gui.py`,
`hermes_cli/dashboard_auth/*` — all CLIENT-SURFACE, production-code.

*The most load-bearing finding is an absence.* Grepping the whole 2,368-file
index for flow-builder, node-editor, graph-editor, canvas, drag-drop and
workflow-builder returns two documentation files about an unrelated
"productivity canvas" and nothing else. Hermes has no visual authoring tool
of any kind, and could not have one: its skills are prose and code, and a
drag-and-drop tool can neither emit nor read back code. This project's
decision to keep a plugin's steps as declared data (`plugin_blueprint.md`
§5.2, closed as D2/D3) is what makes this work item possible at all. There is
no prior art to adopt for the editor itself, and that is a finding, not a
gap.

*Adopted:* one principle, from `dashboard.py`'s own `--insecure` help text —
"as of the June 2026 hardening it no longer disables authentication — a
public bind always requires an auth provider. Bind 127.0.0.1 + tunnel to keep
it local." Hermes learned that a flag which lets a UI bind publicly without
auth gets used. This spec takes the conclusion without the machinery: the
editor binds loopback only and *refuses* any other host, and there is no flag
to override it. That is requirement 9, and it costs one comparison.

*Declined, by name:*

- **FastAPI + uvicorn.** Two heavy new dependencies (plus transitives) for a
  JSON surface with four endpoints, in a project whose entire dependency list
  is `["jsonschema"]`. This project already serves real HTTP with the standard
  library — `channel_webhook.py`'s `ThreadingHTTPServer` — and already
  generalized `gateway_daemon.run(make_server=..., lock_filename=...)`
  (PLUGIN-MARKET-01) so a *second* independent daemon could reuse the
  lifecycle. This editor is the third real member of that seam. Declined for
  the dependency cost specifically, not for style.
- **A Vite/React frontend.** Hermes's own `dashboard --skip-build` flag exists
  because "npm may not be available" in non-interactive contexts, and its
  server command has to be able to skip a build it cannot run. That is the
  named production cost of putting a Node toolchain between a user and their
  UI. This project has no JS toolchain, no `package.json`, and no CI that
  could build one. Declined: the page is hand-written HTML, CSS and JavaScript
  served as static files, with the canvas drawn as inline SVG — a native
  platform feature, not a library.
- **`dashboard_auth/*`'s pluggable auth providers** (basic, drain,
  self_hosted, cookies, tickets, middleware, audit — eleven modules).
  Correct for a product that already has hosted users; here it would be
  speculative infrastructure for a surface that binds loopback only.
  Not foreclosed: the refusal in requirement 9 is the single place a future
  work item replaces with "or an auth provider is configured."

**2. Where this sits relative to the plugin seam, and the bets taken.**

Well after it: the plugin seam is closed and stable (D1–D4, E1, F1, G1–G3),
and this work item does not extend it — it produces the same artifacts the
seam already consumes. Guideline 2's ordinary form applies: future value
should arrive as addition. Three bets are taken, each chosen so reversal is
cheap and local:

- *Standard-library server and hand-written frontend.* Reversible because the
  boundary between them is JSON over HTTP. A future richer frontend replaces
  the files under `editor_assets/` and touches no Python.
- *Positions computed, never stored* (the requester's own decision at design
  time; see Rejected alternatives). Reversible as a pure addition, because
  computed layout must remain the fallback for hand-written and installed
  plugins forever, whatever storage is added later.
- *A hand-written TOML writer* (below). Reversible behind one function name.

**3. The pure/I-O split, and why the browser holds no logic.**

Nothing in this repository can test JavaScript: there is no JS test runner, no
toolchain, and `make test` would not see it. Every decision made in the
browser is therefore a decision `make verify` cannot check. The design rule
that follows — and it binds beyond this work item — is that **the browser
holds no logic that can be held in Python**. Layout positions are computed
server-side and sent as data. Validation status is computed server-side. The
manifest is assembled server-side. The page's job is to draw what it was given
and post back what was drawn.

Files:

- `src/sadana/plugins.py` gains `manifest_to_toml(manifest) -> str` and
  `manifest_from_dict(data) -> Manifest`. Both pure, both the exact inverses
  of things already there (`_parse_manifest`, `manifest_to_dict`), placed
  beside them so the round-trip test sits with both halves. **This is the
  first code in the project that writes a plugin file; everything to date only
  reads one.**
- `src/sadana/editor_layout.py` (new, pure) — `positions(manifest) ->
  dict[str, tuple[int, int]]`. A layered walk from each entry's first step:
  depth from the entry decides the column, order within a depth decides the
  row. Deterministic for a given manifest, which is what makes it testable and
  what makes reopening a plugin show the same picture.
- `src/sadana/editor_server.py` (new, I/O: sockets, disk) — the request
  handlers and the `make_server()` that `gateway_daemon.run` hosts. Per
  CLAUDE.md's rule that a module touching real I/O is its own file. Each
  handler is a thin shell over a pure function taking the request body and
  returning a response value, mirroring `channel_webhook.parse_webhook_request`
  exactly, so the interesting behaviour is unit-testable with no socket.
- `src/sadana/editor_assets/` (new) — `index.html`, `editor.js`, `editor.css`.
  Served as static files by path lookup restricted to this directory.
- `src/sadana/subcommands/editor.py` (new) — `sadana editor [--port]`,
  matching the existing `subcommands/` convention, calling
  `gateway_daemon.run(make_server=..., lock_filename="editor.lock")`.

**4. Draft versus installable — the tension named in intent, resolved by
something that already exists.**

Requirement 7 says an unfinished plugin must save; `plugin_manifest.validate()`
rejects one with `UnresolvedBody`. The resolution needs no new concept:
`validate(check_bodies=False)` already exists — PLUGIN-MARKET-01 built it so a
submitted plugin's *shape* could be checked without executing its code.

The editor never uses `validate()` as a gate on saving. It saves what was
drawn. Separately, it *reports*: `validate(check_bodies=False)` answers "is
the shape sound" (duplicate names, arrows to nowhere, unreachable steps,
cycles — every one of them something the person caused by drawing, and every
one of them worth showing them immediately), while a direct file check answers
"what is this step still waiting for." The person sees both and is blocked by
neither.

**Amended during build, after running it.** That second list was first called
`needs_code` and covered only bodies. An `ask` step whose `SKILL.md` nobody has
written yet came back through the *first* list instead, as a hard
`UnresolvedSkill` problem — so a person drawing a plugin was told two
different stories about one situation: a missing body was a friendly to-do,
a missing skill file was a validation failure. They are the same thing, so
they are now one list, `waiting`, whose entries name the step and what it
waits for (`"code"` or `"a skill file"`), and `UnresolvedSkill` is no longer
counted as a problem. The consequence worth knowing: `validate()` stops at
its first failure, so an unwritten skill hides any structural mistake further
down its sequence until the skill exists — the same one-at-a-time reveal it
already gives for everything else.

That `check_bodies=False` is also the mode that never imports the plugin's
code is not a convenience here, it is requirement 8 and part of requirement 9:
an editor that executed plugin code could never be hosted, and CLAUDE.md's own
rule already says a caller handling input it does not trust uses that mode.

One consequence to state plainly: `discover_plugins()` is the wrong function
for listing plugins here, because it deliberately returns only plugins that
fully validate — an unfinished plugin would vanish from the editor's own list.
The editor lists directories that contain a `plugin.toml` instead, and reports
each one's status rather than filtering by it.

**5. Names that become paths.**

A plugin's name arrives from the browser and becomes a directory under the
plugins root. That is a path-traversal vector, and it is the second time this
class has come up here: PLUGIN-INSTALL-01's own review raised name validation
and it was deferred, when the only caller was the machine's owner. A browser
endpoint changes that. The rule this spec adopts: a name must match
`^[a-z0-9][a-z0-9-]*$` (checked before it touches a path at all), and the
resolved directory must still be inside the plugins root after resolution —
both checks, not either, because the first is the intent and the second is the
proof. Static asset paths are resolved the same way against `editor_assets/`.

**6. Which of the three moves this design makes (guideline 3).**

It **adds a step** — a new command, a new server, a new page — and makes no
existing step heavier or harder. No existing caller pays anything: `plugins.py`
gains two functions nobody is forced to call, and every other module is
untouched.

The cheaper move considered was to make an existing step heavier instead:
bolt the editor's routes onto the gateway daemon's own webhook server, so
there is no second process and no new command. Rejected — it would mean a
daemon whose job is receiving messages also serves an authoring UI, the editor
would only be available when the gateway happens to be running, and the
loopback-only refusal in requirement 9 would collide with the gateway's own
legitimate need to bind publicly one day. Two processes with one shared
lifecycle helper is the cheaper arrangement, and the helper is already there.

**7. State inventory (guideline 4).**

The editor introduces **no persisted state of its own**. Every item:

- *Box positions* — derived, every time, from the manifest's shape
  (`editor_layout.positions`). This is the requester's decision recorded in
  intent's Open questions, and the reason it is affordable is that computed
  layout has to exist anyway for hand-written and installed plugins.
- *The list of plugins* — derived from scanning the plugins root.
- *Validation status and which steps still need code* — derived per request
  from `validate(check_bodies=False)` and a file check.
- *The plugin being edited* — the user's own document, written where the rest
  of the system already looks for it. Not editor state.
- *Server process state* — one lock file, reusing `gateway_daemon.run`'s
  existing single-instance mechanism rather than inventing one.

## Interface

- `manifest_to_toml(manifest: plugins.Manifest) -> str` — emits the sections
  `_parse_manifest` reads. String escaping is delegated to
  `json.dumps(s, ensure_ascii=False)`: TOML basic strings share JSON's escape
  rules for the characters that matter, and `ensure_ascii=False` is required,
  not incidental — with the default, a non-BMP character (an emoji in a
  description) is emitted as a surrogate pair, which TOML does not accept as
  two valid scalars.
- `manifest_from_dict(data: dict) -> plugins.Manifest` — inverse of
  `manifest_to_dict`. Raises `ValueError` (never a bare `KeyError`) naming the
  missing or wrong-typed field, so the endpoint can answer 400 with something
  a person can act on.
- `editor_layout.positions(manifest) -> dict[str, tuple[int, int]]` — pure,
  deterministic, defined for every declared step including unreachable ones.
- `GET /` and `GET /assets/<file>` — the page and its files.
- `GET /api/kinds` → `{"kinds": [{"kind", "uses", "runnable"}...]}` — the
  palette, derived from `NodeKind` and `KIND_USES`. **Added during build**:
  this spec's first draft named four endpoints and left the palette
  unexplained, but requirement 10 needs it served from the declared values
  rather than typed out again in JavaScript, and it belongs to the vocabulary
  rather than to any one plugin.
- `GET /api/plugins` → `{"plugins": [{"name", "status"}...]}`.
- `GET /api/plugins/<name>` → `{"manifest": ..., "positions": ...,
  "problems": [...], "waiting": [{"step", "for"}...]}`.
- `PUT /api/plugins/<name>` — body is a manifest dict; writes `plugin.toml`.
  Returns the same shape as `GET`, so the page redraws from the server's
  answer rather than from what it hoped it sent.
- `POST /api/plugins` — creates a directory, a `plugin.toml` with one entry
  and one step, and a minimal `schema/<tool>.json`, so what is created is
  structurally complete apart from code.
- `POST /api/plugins/<name>/steps` with `{"op": "add"|"rename"|"delete", ...}`
  → the same payload as `GET`. **Added during the build's self-check.** These
  three began as JavaScript, and a review pass pointed out they are graph
  rewrites, not field edits: a rename has to follow every arrow, every route
  port and the entry's own `start`, and a delete has to clear what would
  otherwise dangle. That is exactly the logic this work item's own new
  CLAUDE.md rule says must not live in a browser nothing here can test, so it
  moved to pure functions in `plugins.py` (`rename_node`, `remove_node`,
  `add_node`) behind this endpoint.
- Errors are JSON with a status code: 400 for a malformed manifest or an
  invalid name, 403 for a request that did not come from this machine's own
  page, 404 for an unknown plugin, 409 for creating one that exists. Nothing
  raises out of a handler; the shape mirrors `channel_webhook.py`'s own
  returned-outcome discipline.

**Four hardenings from the build's own self-check**, each demonstrated
against the running code before being fixed (see `review.md`):

- **A save never truncates a working plugin.** `_write_manifest` emits the
  text, parses it back, and only then replaces the file. Found because two
  strings broke the emitter — a lone surrogate (which a browser's own
  `JSON.stringify` produces from half a pasted emoji) crashed the encode
  *after* `write_text` had already emptied the file, and U+007F produced a
  `plugin.toml` that saved and could then never be reopened. Both are fixed
  in `_toml_string` directly, but the round-trip check is the durable part:
  it turns any future emitter bug from "the plugin is gone" into "the save
  was refused and nothing changed."
- **Loopback is not access control against a browser.** A `Host`/`Origin`
  check (`_from_this_machine`) now runs before anything is read or written.
  A self-check demonstrated an `enctype="text/plain"` form on any website
  creating a plugin here as a CORS simple request, and a foreign `Host`
  being answered — the DNS-rebinding shape. Requirement 9's bind refusal
  keeps other *machines* out; this keeps other *websites* out, and it is the
  second place a hosted version replaces with a real session check.
- **A name too long for the filesystem** is refused by a length cap rather
  than raising `OSError` out of `handle()` as a 500 quoting the plugins
  directory's absolute path. Unexpected errors no longer echo their text to
  the browser at all.
- **A plugin's declared name may not diverge from its folder**, because
  `discover_plugins` keys a plugin's identity off the manifest, so allowing
  it would let two folders claim one name.

## Acceptance criteria

- [ ] `manifest_to_toml` → `tomllib.loads` → `_parse_manifest` returns a
      `Manifest` equal to the original, including a hostile string case
      (quote, backslash, newline, tab, emoji) in a description.
- [ ] Every fixture plugin under `tests/fixtures/plugins/` survives that
      round trip unchanged (plugin-a through plugin-d, covering call, ask,
      route, stop, wait and compute steps).
- [ ] `manifest_from_dict(manifest_to_dict(m)) == m` for the same fixtures.
- [ ] `manifest_from_dict` raises `ValueError` naming the field for a missing
      `name`, a missing `kind`, and a non-list `ports`.
- [ ] `positions()` returns a position for every declared step, is stable
      across calls, and places an entry's first step left of its successors.
- [ ] A plugin whose step declares a body no file supplies saves and reloads,
      and its `waiting` list names that step and `"code"` (requirement 7).
- [ ] An `ask` step whose skill file nobody has written appears in `waiting`
      as `"a skill file"` and *not* in `problems` — the asymmetry running the
      editor exposed.
- [ ] A name containing `..`, `/`, or an absolute path is rejected before any
      filesystem access, proven by a test asserting no file outside the
      plugins root is created or read.
- [ ] A request to bind a non-loopback host is refused (requirement 9).
- [ ] The seven kinds are offered and the loop kind is marked unusable, both
      derived from the declared `NodeKind` values rather than a list typed
      twice (requirement 10).
- [ ] `scripts/prove_editor_e2e.py`: a real server, a real HTTP round trip —
      create a plugin, read it back, modify a step, save, reload, and confirm
      the file on disk parses to what the browser last saw. Output pasted into
      review.md's `## Evidence`, per CLAUDE.md's rule for a block's first real
      external round trip.

## Non-goals

- Writing a step's code in the browser. Deliberate, from intent.
- Editing what arguments a plugin's entry accepts (the parameters schema).
  A new plugin gets a minimal one scaffolded; changing it is a later work
  item. Named in Concerns as a real limitation on what a creator can express.
- Accounts, sign-in, permissions. The loopback refusal stands in for them.
- Running or testing a plugin from the editor.
- Installing, publishing or submitting from the editor — PLUGIN-INSTALL and
  PLUGIN-MARKET already own those and this item does not touch them.
- Undo history, multi-user editing, or any collaborative behaviour.
- Editing more than one plugin at a time in one page.

## Rejected alternatives

- **Storing box positions** (in the manifest, or in a sidecar file beside it).
  Put to the requester at design time with the costs written out; they chose
  computed layout. The argument that decided it: hand-written and installed
  plugins can never carry coordinates, so computed layout must exist in every
  design — the two storage options are "build that *and* a storage mechanism,"
  and the manifest option additionally puts presentation data inside the thing
  the runtime executes and changes a format three closed work items depend on.
- **Serving the editor from the existing gateway daemon.** See Design point 6.
- **FastAPI/uvicorn and a Vite/React build.** See Design point 1, with the
  specific costs hermes itself documents.
- **Adding `tomli-w`** for the writer. A new dependency for what the manifest's
  shape needs in about twenty-five lines — three tables and two arrays of
  tables, no dates, no floats, no nesting — with escaping delegated to the
  standard library, and a round-trip test that fails loudly if the hand-rolled
  version is ever wrong. This is the ladder's "never add a dependency for what
  a few lines can do," and the round-trip test is what makes it safe rather
  than merely cheap.
- **Gating saves on `validate()`.** It would make requirement 7 impossible and
  would force a second "draft" storage location — a new format, which
  requirement 4 forbids.
- **Computing layout in the browser.** It is presentation logic and it belongs
  there in most projects; here it would be logic `make verify` cannot see. See
  Design point 3.

## Concerns

The frontend is genuinely untested by anything in this repository, and no
amount of keeping logic in Python removes that: the drag-and-drop
interactions, the SVG rendering and the click handling are exactly the part a
non-programmer experiences, and `make verify` will report green whatever state
they are in. The e2e script proves the server and the round trip, not that a
box can be dragged. A reviewer should read the JavaScript with that in mind,
because nothing else will. This is the sharpest tension in this design between
`testing-conventions` (which cannot reach the browser) and intent (whose whole
point is what happens in the browser), and it is resolved by moving as much as
possible out of the browser rather than by pretending the remainder is
covered.

Second, this is the largest work item this project has attempted, and the
requester reversed an earlier decision to split it after being shown the cost.
The pure/I-O split above is what keeps that affordable to review: a reviewer
can read `plugins.py`'s two new functions, `editor_layout.py` and
`editor_server.py`'s pure handlers as ordinary Python with ordinary tests, and
read the assets separately as the untested part.

Third, a creator cannot yet say what arguments their plugin takes — the
parameters schema is scaffolded and then untouchable from the editor. For a
plugin whose entry step consumes its arguments, that is a real ceiling on what
can be drawn without a programmer, and it narrows requirement 3's "runs with
nothing converted" to "runs, taking no arguments." Named as a Non-goal rather
than hidden, and worth being the next work item in this track.

Fourth, a save rewrites `plugin.toml` from the manifest, so a hand-written
file's comments and any keys this project does not model are lost the first
time someone opens that plugin in the editor and saves it. The round trip
this spec promises is of the *manifest*, not of the file's text, and nothing
in `Manifest` has anywhere to keep a comment. Worth knowing before pointing
the editor at a plugin somebody documented carefully; making it
comment-preserving means a different kind of TOML handling entirely and
belongs to its own work item.

Fifth, `editor_assets/` is package data, and this project has never shipped
any. It resolves correctly from a source checkout, which is the only way
sadana runs today; an installed wheel would need `setuptools` package-data
configuration that does not exist yet. Not solved here because no consumer
exists, but a reviewer should notice that the first `pip install` of this
project will surface it.
