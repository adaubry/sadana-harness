# Spec: Somewhere to put what a plugin makes

Intent: docs/tasks/ARTIFACT-STORE-01-somewhere-to-put-what-a-plugin-makes/intent.md

## Requirements

1. **A running plugin can ask for a directory to write into**, and gets one
   that belongs to that run alone. *(intent: "Every run of a plugin has a place
   of its own to write files".)*
2. **It is created on the first ask, not before.** A run that never asks leaves
   nothing on disk. *(intent: "created only when something actually writes to
   it".)*
3. **Its path is derived from the key the run already has** —
   `(conversation_key, turn_seq, seq_in_turn)`, the primary key
   `observability.plugin_runs` already uses — never a freshly minted id.
   *(intent Constraints; CLAUDE.md: "A derived/observability record's key
   reuses whatever unique key its producing call already minted".)*
4. **It lives under `state_dir`**, so the one setting a person has already
   moved moves these too. *(intent: "under the directory this project already
   uses".)*
5. **A `call` node at any depth can ask**, not only the entry node.
6. **A returned `Artifact` of `kind="file"` whose `ref` resolves outside that
   run's directory fails the node**, rather than being recorded. Both halves
   of CLAUDE.md's path rule apply to the conversation key that becomes a
   directory name: an allowlist-shaped encoding, and a containment re-check
   after resolution. *(intent: "a step that tries to write outside its own
   run's place is stopped".)*
7. **A resumed run writes to the same directory its pre-pause half did.**
   *(intent: a run's place belongs to the run, and a resume is the same run.)*
8. **`Artifact.ref`'s documented meaning becomes true.** It has said "a path
   under the run's own output directory" since G2 while nothing created one.
9. **A plugin that writes nothing is unaffected**, and every existing test
   passes without edit.

## Design

A new module owns the directory, a contextvar carries it to whatever is
running, and `run_graph` is what binds it for the length of a walk. The
argument worth having is about that contextvar, and it is below.

### Where the code lives

**`src/sadana/artifact_store.py`** (new). It touches the disk on every call, so
it is its own file rather than part of `plugins.py` — CLAUDE.md keeps an I/O
module separate from a block's pure-function module, and unlike the environment
reads `plugins.py` already does, this one creates directories.

- `run_dir_name(conversation_key: str) -> str` — pure. The encoding in
  requirement 6.
- `run_dir(state_dir, conversation_key, turn_seq, seq_in_turn) -> Path` — pure
  path arithmetic, creates nothing.
- `activate(directory: Path | None)` — a context manager setting the contextvar
  for the duration of a walk and restoring it after, `None` meaning "no run is
  current".
- `output_dir() -> Path` — what a node body calls. Creates the directory on
  first call (`mkdir(parents=True, exist_ok=True)`) and returns it. Raises
  `NoOutputDirectory` when no run is current, or when the current run has none
  — never returns a path that would land somewhere arbitrary.
- `contains(directory: Path, candidate: str) -> bool` — the containment check
  requirement 6 needs, resolved on both sides.

**`src/sadana/plugin_manifest.py`** — `run_graph` gains
`output_dir: Path | None = None`, wraps its walk in `artifact_store.activate()`,
and extends the existing `isinstance(value, plugins.Artifact)` arm: a
`kind="file"` artifact whose `ref` is not inside the active directory fails that
node through the `failed()` path already there.

**`src/sadana/plugin_dispatch.py`** — `dispatch()` already holds `turn_key` and
the closure-local `seq_in_turn`, which is the whole key requirement 3 names, so
it computes the directory and passes it. `resume_paused_run()` reads the two
new columns below and passes the same one.

**`src/sadana/conversation_store.py`** — `plugin_pauses` gains `turn_seq` and
`seq_in_turn`, added by the idempotent `PRAGMA table_info` + guarded
`ALTER TABLE ... ADD COLUMN` shape CLAUDE.md prescribes, with no backfill. A
legacy row has neither, reads back as `None`, and a run resumed from one gets
no output directory — exactly its pre-fix behaviour, self-correcting the next
time that pause is rewritten.

**`src/sadana/plugins.py`** — one docstring line on `Artifact.ref`, which has
been describing a thing that did not exist.

### The directory name, and why it is encoded rather than used raw

`<state_dir>/artifacts/<conversation>/<turn_seq>/<seq_in_turn>/`.

`turn_seq` and `seq_in_turn` are integers this project mints and are safe as
they stand. The conversation key is not: `sadana chat --key NAME` takes an
arbitrary string from the command line, and existing conversations are free to
be named with a slash, a dot-dot or a leading dash. Refusing those names is not
available — it would break conversations that already work, in a work item
about writing files.

So the key is *encoded*, not validated: every character outside `[a-z0-9._-]`
becomes `-`, and a short hex digest of the original key is appended. The digest
is not a new identity and requirement 3 still holds — it is a pure function of
the key that already exists, computed fresh every time, stored nowhere. It is
there so that two keys that differ only in characters the encoding flattens
cannot land in one directory. The result stays readable
(`debugging-session-4f3a1c2b`), which matters because a person reads this path
out of a plugin's answer.

The encoding is the allowlist half. `contains()` after resolution is the other
half, and both run — never either (CLAUDE.md).

### Why a contextvar, when the last work item rejected exactly this shape

`PLUGIN-CONFIG-01` faced the same question — how does a value reach a `call`
node three steps deep — and answered it without any ambient state: a setting is
a function of the *plugin's* identity, and a body knows its own plugin's name,
so it can simply look the value up. That answer is not available here. A run's
directory is a function of the *run*, and a body cannot know which run it is
in: nothing in `plugin.toml`, in the body's signature, or in the threaded value
names the conversation or the turn.

The three alternatives and why each is worse are in Rejected alternatives. The
honest summary is that this is the case ambient state is actually for — a
value that is constant for the length of one call tree, that every frame in it
may need, and that no frame can derive. A `contextvar` is the scoped version of
it: it is per-task, so two conversations running concurrently under the gateway
cannot see each other's directory, which a module-level global would get wrong.
It propagates into `asyncio.to_thread`, which is how `call` bodies are run, so
the one node kind that matters is covered without special handling.

It is still ambient state and guideline 4 is still right to dislike it. What
keeps it honest: nothing stores it, `activate()` restores the previous value on
exit so a nested run cannot leak into its parent, and `output_dir()` raises
rather than guessing when no run is current.

### Which of the three moves this makes

The scenario is "a plugin produced a file and there is nowhere for it to go".

It **adds one step** — the containment check on a returned file artifact — and
that step runs only for a node that returns a `kind="file"` `Artifact`, which
is a population of zero today. Every existing run pays nothing: `activate(None)`
sets a contextvar and restores it, and the `isinstance` arm it extends is
already on that path from G2.

No existing step is made heavier or harder. There was a cheaper-looking
placement — check at write time inside `output_dir()` rather than at return
time — and it is not actually cheaper, because a body is free to construct a
`ref` it never wrote to; the value crossing the boundary is the thing worth
checking, which is G2's own reasoning for checking the returned value at all.

### Policies applied

- **`testing-conventions`** — unit tests against `tmp_path` with the existing
  autouse state-directory fixture; no test writes to a real state directory; a
  contextvar is real machinery being exercised, not a faked environment.
- **CLAUDE.md, "a derived record's key reuses whatever unique key its producing
  call already minted"** — requirement 3.
- **CLAUDE.md, "a caller-supplied name that becomes a filesystem path"** —
  requirement 6, both halves.
- **CLAUDE.md, "a new persisted column… idempotent `PRAGMA table_info` +
  guarded `ALTER TABLE`, never a backfill migration"** — the two pause columns,
  with the documented no-safe-default fallback.
- **CLAUDE.md, "a module that touches real I/O is its own file"** — the new
  module exists because of this rule, not despite it.
- **CLAUDE.md, "a plugin's outcome crosses back to its caller only as a
  returned `DagResult`"** — a bad `ref` fails the node; it does not raise.
- **CLAUDE.md, "a registry… earns its cost only once a second real member
  exists"** — one store, no backend seam.
- `project-structure` and `reference-lookup` are not present in
  `.claude/skills/`; the corpus consultation below was done by hand.

### What the reference does, and what is taken from it

hermes has two answers, and they disagree with each other.

**Its main answer is a global per-capability cache.**
`tools/tts_tool.py:264-268` computes `DEFAULT_OUTPUT_DIR` once at import from
`get_hermes_dir("cache/audio", "audio_cache")`; images use `cache/images` the
same way. **Declined, on two counts.** A shared directory needs every file in
it to have a unique name, which means minting an id per file — the thing
CLAUDE.md's key rule exists to prevent — or accepting collisions. And it severs
the file from the run that made it, so "which run produced this?" has no answer.
A per-run directory gets uniqueness for free from a key that already exists.

**Its other answer, for cron, is exactly ours.**
`cron/jobs.py:190-192` — `get_cron_output_dir() / job_id`, a directory per
scheduled job named by the id that job already has. **Adopted.** That hermes
reached for per-run naming precisely where a run had a durable identity, and
for a global cache where it did not, is the evidence for this shape rather than
a preference for it.

**`get_hermes_dir`'s dual-layout resolution** (`hermes_constants.py:384-410`) —
a new path, a legacy path, and a rule about when an empty legacy directory does
*not* count, carrying a referenced production regression. **Declined**, and
worth naming: it is compatibility debt for installs that predate a move. This
project has no such installs and importing the mechanism would be importing
someone else's history.

**Computing the directory once at import** (`DEFAULT_OUTPUT_DIR`, a module
constant). **Declined.** Every path in this project resolves fresh per call —
`config.get_paths()` says "No caching: nothing here is expensive enough to
justify stored state that could go stale", and `plugins._plugins_root()` takes
the same posture. An import-time capture also goes stale under the test fixture
that redirects the state directory, which is how a suite quietly starts
depending on the developer's machine.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| the run's directory path | derived, per call | a pure function of `state_dir` and a key that already exists |
| the encoded conversation name | derived, never stored | storing it would be a second name for the conversation, able to disagree with the first |
| whether the directory exists | derived — `mkdir(exist_ok=True)` | asking is the same cost as remembering, and remembering can be wrong |
| the active directory, during a walk | the contextvar — scoped, restored on exit, stored nowhere | the one thing here that cannot be derived by the code that needs it |
| `turn_seq` / `seq_in_turn` on a pause | stored, two new columns | a resume happens in a later process; there is nowhere else for it to come from |

The files themselves are the only durable new state, and they are the point.

## Interface

**For a node body:**

- `artifact_store.output_dir() -> Path` — the run's directory, created on first
  call. Raises `artifact_store.NoOutputDirectory` when no run is current.

**For `run_graph`:**

- new keyword `output_dir: Path | None = None`. `output_dir()` raises, and a
  `kind="file"` artifact is refused: with no directory, every path is outside
  it.

  **Corrected during Build, 2026-09-11.** This bullet originally read "`None`
  keeps today's behaviour exactly… no containment check can fire because there
  is nothing to be contained by", which contradicts the code and is wrong. It
  is a deliberate behaviour change and belongs stated as one: before this work
  item a `call` body could return a `file` artifact pointing anywhere and have
  it recorded, because `G2-artifacts` § Rejected alternatives declined to
  validate one — on the explicit grounds that "the later file-writing item is
  free to add real handling when the underlying concept exists". This is that
  item, and failing closed is that handling. Both production callers pass a
  directory; the one path that still passes `None` is a run resumed from a
  pause row written before this work item, which now cannot emit a file
  artifact where it previously could. That is the safer of the two failures and
  it is named here rather than discovered.
- a `call` node returning `Artifact(kind="file", ref=…)` outside the active
  directory is failed with the same shape every other node failure uses —
  `failed_node` set, one generic sentence, nothing raised.

**On disk:** `<state_dir>/artifacts/<encoded-conversation>/<turn>/<seq>/`.

**Errors:** `NoOutputDirectory` is raised only into a body that asked out of
turn, where it becomes that node's failure through `run_graph`'s existing
`except`. Nothing new escapes `run_graph`.

## Acceptance criteria

- [ ] A `call` body calling `output_dir()` gets a directory that exists, under
      `state_dir`, and writing a file into it succeeds.
- [ ] A run whose body never calls `output_dir()` creates no directory —
      asserted by checking `state_dir/artifacts` does not exist.
- [ ] Two runs differing only in `seq_in_turn` get different directories; two
      conversations whose keys differ only in a character the encoding flattens
      (`a/b` and `a-b`) also get different directories.
- [ ] A body at the *second* node calls `output_dir()` successfully.
- [ ] A body returning `Artifact(kind="file", ref="/etc/passwd")` fails that
      node, and `/etc/passwd` is untouched.
- [ ] A body returning `Artifact(kind="link", ref="https://…")` is unaffected.
- [ ] `output_dir()` outside any run raises `NoOutputDirectory`; inside a run
      started with `output_dir=None` it also raises.
- [ ] `activate()` restores the previous value, proved by a nested activation.
- [ ] A resumed run writes into the same directory its first half did.
- [ ] A pause row written before this change still resumes, with no output
      directory and no error.
- [ ] Every existing test passes unedited.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Deleting anything, ever: no expiry, no size cap, no cleanup. Named in
  `intent.md`'s open questions as the owner's decision and a real cost.
- A way to browse or list what has been produced. The reference already travels
  back in the plugin's answer.
- Recording artifacts in `plugin_runs` or anywhere else observability reaches.
- More than one file per node. A node returns one value, so it emits one
  `Artifact` — a pre-existing G2 limit, not touched here.
- Letting `compute`, `route`, `ask` or `stop` emit an artifact. G2 declined it;
  this item does not reopen it.
- Any sandbox or real confinement of what a plugin process can write.

## Open questions

**Absolute or relative `ref`?** This spec writes the absolute path, because the
model repeats it to a person and a person needs something they can act on. The
cost is that it stops being correct the moment `SADANA_STATE_DIR` moves or the
files are copied. A relative ref survives that and needs the reader to know
what it is relative to. `intent.md` raises it; the owner may overrule.

**Should the conversation key simply be required to be path-safe?** Encoding
avoids breaking existing conversations, at the price of two names for one
conversation. Requiring a safe key at the point `--key` accepts it would be
cleaner and is a breaking change to a surface outside this work item.

## Rejected alternatives

**The body writes to a temporary file and the runtime moves it into place.**
Genuinely attractive, and the strongest alternative: no channel at all, no
ambient state, works at any depth for free, and the body never learns the run's
directory, so writing outside it becomes structurally impossible rather than
checked. Rejected on one specific consequence: the runtime would be moving a
path the body chose, so a body returning `ref="/etc/passwd"` would have the
runtime *delete* it out of its own directory. Copying instead of moving removes
the destruction but adds a full copy of every generated video, and the
in-process trust boundary makes the read half moot anyway. Handing out a
directory keeps large media a single write, and the containment check catches
the bad `ref` without acting on it first.

**A `_sadana_output_dir` reserved key in `arguments`.** The convention CLAUDE.md
names, and the one `PLUGIN-CONFIG-01` used for the memory account. It reaches
the entry node only — `run_graph` threads one value, each node receiving its
predecessor's output — so a plugin shaped `compute` → `call` would have to pass
its own output directory forward as data to reach the step that writes. That is
the same reasoning that rejected it for settings, and it applies harder here:
the artifact-emitting `call` is rarely the first node, because a node returns
one value and a plugin producing both a report and a file already needs two
(capability blueprint §6 Rule 5).

**A second parameter on the body signature, `def fetch(value, output_dir)`.**
Changes the call contract for every body ever written, including the ones that
never produce a file, and CLAUDE.md refuses a hidden parameter threaded through
a body's own signature for exactly this class of value.

**A module-level global instead of a contextvar.** Smaller, and wrong the first
time the gateway runs two conversations at once: they would overwrite each
other's directory and a plugin would write into a stranger's conversation. The
contextvar costs nothing extra and is right under concurrency.

**Keying the directory by conversation only, not by turn and sequence.** Fewer
directories and a simpler resume story, since a pause already carries its
conversation key. Rejected because it loses the property requirement 3 is for:
`plugin_runs` is keyed by all three, so a directory keyed by one cannot be
lined up against the record of the run that filled it, and two runs in one
conversation would write into each other's space.

**Adding a `sadana artifacts` listing command.** Out of scope per `intent.md`,
and it would need the retention answer first to know what it is listing.

## Concerns

**The contextvar is the thing to review hardest, and I am not fully comfortable
with it.** It is ambient state introduced deliberately in a project whose design
guidelines say to minimise exactly that, and the previous work item rejected a
weaker form of the same idea. The distinction I am relying on — that a setting
is a function of the plugin, which a body knows, while a directory is a function
of the run, which it cannot — is real, but it is the kind of distinction that
sounds better in a spec than it reads in code six months later. The mitigation
is that the surface is one function, and if `output_dir()` ever needs a second
argument that is the signal the shape was wrong.

**Requirement 7 costs two columns on a table that already ships.** The
alternative was letting a resumed run write somewhere unrelated to its first
half, which breaks the one property this design is built around. The migration
shape is the one CLAUDE.md prescribes and the fallback is genuinely safe, but a
schema change is still the most expensive thing in this diff and the least
related to the headline feature. A reviewer who wants requirement 7 dropped to
avoid it would be making a reasonable trade.

**Confinement is addressing, not isolation — the same sentence the last work
item had to write, and it must not be read as stronger here.** A `call` body
runs in-process (`plugin_blueprint.md` §10 Risk 1). Nothing stops it opening any
path the process can open. What is checked is the `ref` that crosses back, so a
plugin cannot *record* an artifact outside its directory and cannot mislead a
reader about where something is. It can still write wherever it likes. Real
confinement waits on the subprocess primitive — capability blueprint §5.6 gap 1
and §7 OQ3 — and this item must not be cited later as having provided it.

**Nothing deletes anything, and the first real user is image generation.**
That is blueprint item 4, immediately after this one. The disk grows from the
moment that lands, and the retention question is unanswered. This is stated in
`intent.md` and repeated here because it is the most likely thing to bite in
practice and the easiest to forget while it is still theoretical.

**`state_dir/artifacts` becomes a second kind of content in a directory that so
far holds a database, a secrets file and installed plugins.** That is a mild
layering smell; the alternative is a second configurable location, which
`intent.md` explicitly rules out and which would give a person two settings to
keep in step.
