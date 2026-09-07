# Spec: A plugin's own description of itself can be trusted before anything runs

Intent: docs/tasks/D2-manifest-validation/intent.md

## Requirements

1. A plugin's own file describing itself — its identity, the tools it
   exposes, and the steps behind each one — can be read into a value the
   rest of the system can use, or rejected with a specific, named reason.
   Traces to intent's Proposed outcome ("the system can read a plugin's own
   description of itself and say... whether that description can be
   trusted").
2. Seven checks run against that value, each independently named when it
   fails: the file parses; every tool's parameter schema file exists and is
   a structurally valid JSON Schema; every step that asks the model for
   judgement names a real, loadable skill; every step has a name used only
   once; every step's declared next step (or named branch) points at a step
   that actually exists; every step that runs code names a real, resolvable
   piece of that code; and every declared step can actually be reached from
   at least one of the plugin's entry points. Traces to intent's Proposed
   outcome (the design material's `§8` list, which the intent's Constraints
   commit this item to closing in full) and to intent's Changed during
   planning (all six — now counted as seven, `§8`'s "parses" and "schema
   valid" are two separate checks, not one — confirmed in scope together).
3. A failure is a specific, named answer — which check failed and, where
   relevant, which step or file caused it — never an unhandled exception
   reaching whoever asked for the validation. Traces to intent's Proposed
   outcome ("a specific, nameable answer rather than stopping with an
   unexplained error") and Constraints (reuse the classified-answer pattern
   already proven elsewhere).
4. Nothing this item's own code does runs a single step of what the
   plugin's file declares. Reading `init.py` to confirm a named piece of
   code exists there is as far as this goes; nothing calls it. Traces to
   intent's Constraints ("does not run a single step of a plugin's declared
   sequence").
5. This item does not fetch a plugin, install one, resolve its third-party
   dependencies, or change what the model is shown about an installed
   plugin. Traces to intent's Constraints (all three named directly).

## Design

**Where this lands.** D1 (already approved, landing first per the design
material's own ordering note — "every later item is cheaper once it has
landed") adds `src/sadana/plugins.py` as this block's pure-dataclass module.
This item extends that module with the pure pieces of its own contract, and
adds one new module, `src/sadana/plugin_manifest.py`, for everything that
touches a real file — CLAUDE.md's rule that disk-touching code is its own
file, separate from a block's pure module, applies directly: this item's
whole job is reading files.

**Relocating skill-loading out of `conversation.py`.** `conversation.py`
already has `SkillRef`, `_plugins_root()`, `_skill_path()`, `load_skill()`,
`SkillLoadError`, and `_parse_skill_md()` — built for C9's `run_child`, and
`_plugins_root()`'s own docstring already says its real owner is "a PLUGINS
block that doesn't exist yet." This item is that block landing, and this
item's own `§8` check 3 ("every `ask` node's skill resolves") needs exactly
this logic — reusing it, not rebuilding a second copy, per CLAUDE.md's "no
second way to do X." The split follows the same pure/I-O line as everything
else here:

- `plugins.py` (pure) gains `SkillRef`, `SkillLoadError`, `_parse_skill_md`,
  `_skill_path`, `_plugins_root` — none of these touch a real file; the last
  two only resolve a `Path` from an env var and string joins, no
  `read_text`/`exists` call anywhere in them.
- `plugin_manifest.py` (I/O) gains `load_skill` — the one piece of that
  group that actually calls `path.exists()` and `path.read_text()`.

`conversation.py`'s one call site (`run_child`, currently
`load_skill(spec.skill)`) becomes `plugin_manifest.load_skill(spec.skill)`,
and its import line adds `plugin_manifest` alongside the `plugins` import
D1 already adds. `SkillRef` stays referenced from `conversation.py`
(`ChildSpec.skill: SkillRef`) via `from sadana import plugins` — a type
used, not owned, by CONVERSATION, the same relationship `conversation.py`
already has with `model_access.Usage`.

**Parsing.** `tomllib` (stdlib since Python 3.11, this project's own
floor) reads `plugin.toml`. No new dependency for this part — a TOML
parser was never missing, only unneeded until now.

**The parsed shape**, in `plugins.py` (pure — these are data, not readers):

```python
@dataclass(frozen=True)
class Entry:
    tool: str
    purpose: str
    parameters: str  # path to a schema file, relative to the plugin's own root
    start: str        # the node name execution would begin at

@dataclass(frozen=True)
class Node:
    name: str
    kind: Literal["compute", "ask", "route", "stop", "call", "each", "wait"]
    body: str | None = None    # "module:function", for compute/call/route
    skill: str | None = None   # a skill name, for ask
    next: str | None = None    # a single successor node name
    ports: tuple[str, ...] = ()  # named successor node names, for route

@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    description: str
    entries: tuple[Entry, ...]
    nodes: tuple[Node, ...]
```

**The outcome set**, in `plugins.py` (frozen dataclasses, same shape as
`model_access.Outcome`):

```python
@dataclass(frozen=True)
class Valid:
    manifest: Manifest

@dataclass(frozen=True)
class ManifestParseError:
    detail: str

@dataclass(frozen=True)
class InvalidSchema:
    entry: str
    path: str
    detail: str

@dataclass(frozen=True)
class UnresolvedSkill:
    node: str
    detail: str

@dataclass(frozen=True)
class DuplicateNodeName:
    name: str

@dataclass(frozen=True)
class DanglingTarget:
    node: str
    target: str

@dataclass(frozen=True)
class UnresolvedBody:
    node: str
    body: str

@dataclass(frozen=True)
class UnreachableNode:
    node: str

ManifestOutcome = (
    Valid | ManifestParseError | InvalidSchema | UnresolvedSkill
    | DuplicateNodeName | DanglingTarget | UnresolvedBody | UnreachableNode
)
```

Eight members for seven checks, not seven: a parse failure has to be its
own outcome, because none of the other six checks have anything to run
against without a parsed structure first.

**The validator**, in `plugin_manifest.py` (I/O — the whole point of this
function is reading files):

```python
def validate(plugin_dir: Path) -> plugins.ManifestOutcome:
```

Runs the seven checks in the order `§8` lists them, stopping and returning
the first failure — the same fail-fast posture `model_access.classify()`
already has (one outcome per call, not a batch). Order: parses; schema
files exist and are valid JSON Schema; skills resolve; names unique; edges
target declared nodes; bodies resolve; graph reachable from an entry —
each check assumes every earlier one already held, which is why a parse
failure has to come first and reachability has to come last (it needs the
full, already-name-checked, already-edge-checked node set to walk).

**Schema validation.** `§8` says a schema file must be "valid JSON
Schema," not merely valid JSON — a real, structural check, which the
stdlib alone cannot do. `jsonschema` (already present in this
environment's dependency graph, `4.26.0`, but not yet a declared
dependency of this project) becomes `sadana`'s first declared runtime
dependency, added to `pyproject.toml`'s `dependencies`. Confirmed with the
user directly rather than assumed: the alternative (a shallow "parses as
JSON and is an object" check) was on the table and declined in favor of
conforming to `§8` as written.

**Body resolution.** A `body = "init:fetch_record"` reference is checked
by importing the plugin's `init.py` (`importlib.util.spec_from_file_location`
— the same dynamic-loading approach `model_providers` already uses for
per-directory Python files, not `import`, so a hyphenated plugin directory
name never collides with another the way a regular package import would)
and confirming `fetch_record` exists on it and is callable. See Concerns:
importing a module runs its top-level code, which is a real, named cost of
this specific check.

**Reachability.** A plain breadth-first walk from every `entry.start`,
following `next` and `ports`, over the plugin's own `Node` values — reading
the declared graph's shape, not running any node. This is not "the graph
walker" `§11` item 4 names: that walker executes a node's effect and
accumulates a `NodeTrace` at run time, against a real input. This is a
static, one-time reachability check over declared edges, with no run
happening and nothing accumulated. Conflating them would be building item
4 early; keeping this to "which nodes does the declared graph name that no
edge or entry ever points at" keeps it a `§8` check, and only that.

## Interface

**In:** `validate(plugin_dir: Path) -> plugins.ManifestOutcome` — one
`Path` to an installed plugin's own root directory (the same directory
shape `§5.1` already fixes: `plugin.toml` at the top, `skills/` and
`deps/` and `init.py` beside it).

**Out:** one of the eight `ManifestOutcome` members. `Valid` carries the
parsed `Manifest`; every other member carries only what a person or a
later caller needs to act on the failure — which node, which file, one
line of detail. None of them is an exception subclass; `ManifestOutcome`
is a plain union, not raised.

**Errors:** this item's own code raises nothing for an expected validation
failure. `validate()` itself can still raise for a scenario that is a real
bug rather than an expected outcome of validating someone's plugin file —
an unreadable filesystem, a `plugin_dir` that doesn't exist at all — the
same posture `load_skill()` already has for a missing `SKILL.md`'s parent
directory.

## Acceptance criteria

- [ ] `src/sadana/plugins.py` exports `Entry`, `Node`, `Manifest`,
      `ManifestOutcome`, and its eight members.
- [ ] `SkillRef`, `SkillLoadError`, `_parse_skill_md`, `_skill_path`,
      `_plugins_root` are defined in `plugins.py`, not `conversation.py`.
- [ ] `src/sadana/plugin_manifest.py` exports `load_skill` and `validate`;
      neither is defined anywhere else.
- [ ] `conversation.py`'s `run_child` calls `plugin_manifest.load_skill`;
      no reference to a module-local `load_skill` remains there.
- [ ] `pyproject.toml`'s `dependencies` includes `jsonschema`.
- [ ] Each of the seven `§8` checks has at least one unit test proving
      `validate()` returns the specific outcome member named for it, using
      a hand-built `tmp_path` plugin directory — no real plugin, no
      network, per `testing-conventions`.
- [ ] A well-formed fixture plugin directory (new, under `tmp_path` in the
      test, not committed under `tests/fixtures/plugins/` — those two
      fixtures don't have a `plugin.toml` yet, and giving them one is
      outside this item, which touches no proof-script fixture) validates
      as `Valid` with every field of the parsed `Manifest` matching what
      the fixture declared.
- [ ] `mypy src` passes with `jsonschema` installed.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Fetching, installing, or verifying a plugin's tag against its source —
  `§9`'s Installation boundary, unchanged by this item.
- Producing a `PluginCatalogEntry` or `ToolSpec` from a validated manifest
  — `§11` item 3, separate work.
- Running any node, in any sense — the graph walker is `§11` item 4.
- Resolving `deps/` — `§9`'s own named boundary, deferred project-wide.
- A second entry point that lets an already-`Valid` manifest be
  re-validated cheaply after one field changes — nothing caches a result
  today, and nothing needs to.

## Rejected alternatives

**(Guideline 1 — reference corpus.)** hermes-agent's own plugin manifest
(`hermes_cli/plugins.py`'s `PluginManifest`, and `agent/pet/manifest.py`)
validates structurally too, but as Python — a manifest there is importable
code with a `class Plugin(...)` a loader instantiates, not declarative
data a loader reads. Declining that shape here is not new to this item —
`§5.2` of the design material already made this call project-wide ("the
graph is data, the node bodies are code") for reasons this item didn't
re-litigate: a Python-defined manifest cannot be displayed by a future
visual editor without executing it, which `§3.2`/`§3.5` already rule out.
This item's own, narrower question — validate declarative TOML data,
stdlib parser plus one added dependency for the one piece stdlib can't
do — has no close hermes analogue to adopt or decline, since hermes never
validates a plugin's shape without importing it.

**(Guideline 2 — reduce the number of bets, pre-seam case.)** Still
pre-seam: no installed plugin, no consumer of a `Valid` manifest exists
yet (item 3 is that consumer). `Manifest`/`Node`/`Entry` carry only the
fields `§5.2`'s own example already commits to (`name`, `kind`, `body`,
`skill`, `next`, `ports`, plus the entry-level fields) — no
forward-looking field for `each`'s loop variable or `wait`'s trigger
shape, both explicitly deferred (`§6`, `§7.2`). Adding them now, unused,
would be exactly the foreclosed-by-guideline-2 move; `Node`'s existing
`Literal` already includes `each`/`wait` as kinds (matching D1's own
`NodeTrace.kind` decision, same reasoning: the closed set is decided,
naming it isn't building it) without giving either kind fields nothing yet
reads.

**(Guideline 3 — least step-cost.)** The central choice — seven ordered,
fail-fast checks over one parsed value, each returning a distinct named
outcome on failure — makes the existing "read a plugin's file" step
heavier (more is checked before anything is trusted) rather than adding a
new step to some other part of the system or making an existing step
harder to call. A cheaper-looking alternative, collecting every failing
check into one batch outcome instead of stopping at the first, was
considered and declined: `model_access.classify()` already established
one-outcome-per-call as this project's precedent, and a batch result would
be a second shape for the same idea, invented here for a convenience (a
plugin author seeing every problem at once) nothing in the intent asked
for. If that convenience is ever wanted, it is a cheap, additive change to
`validate()`'s return type later — not a reason to build it speculatively
now.

**(Guideline 4 — minimise mutable state.)** No mutable state. Every new
type is `frozen=True`; `validate()` is a pure function of `plugin_dir`'s
contents at the moment it's called — nothing cached, nothing invalidated,
nothing that goes stale, because a second call just reads the files again.
The one open question this leaves (below) is whether that "just re-read
it" posture is still right once real plugins are validated on every
process start rather than once in a test.

## Open questions

Carried forward from `intent.md`: whether validation runs once at process
start for every installed plugin, or lazily per plugin when first needed.
This item's own design doesn't need to answer it — `validate()` is a pure
function either caller can call — but whichever future item wires a real
caller in decides it, and that decision interacts with whether a result is
ever cached (this item stores nothing, deliberately, per guideline 4
above).

## Concerns

**Policy skills consulted.** `testing-conventions` was loaded and applied
— see the Acceptance criteria's fixture posture (`tmp_path`, no real
filesystem, no network). `project-structure` and `reference-lookup` do not
exist as skills in this repository, same as noted in `D1-dagresult-dispatch/spec.md`;
this design follows `docs/reference/plugin_blueprint.md` directly and
consults the reference corpus (guideline 1, above) by reading hermes's own
manifest files directly.

**The real, unresolved tension.** Checking that a `body` resolves means
importing the plugin's `init.py` as a Python module — which executes
whatever top-level code that file has, before this item's own code ever
calls the specific function named. `§9` puts sandboxing outside this
item's scope, and `§10` Risk 1 already names "a `call` node runs plugin
code in-process until EXECUTION lands" as accepted while every plugin is
first-party. This item's own import-to-check-a-name step is a narrower
version of the same accepted risk, one step earlier than Risk 1 describes
(at validation time, not at call time), and it is accepted on the same
basis: every plugin validated today is first-party, and there is no way
to confirm a dotted reference resolves to a real, callable name without
either importing the module or parsing its source as an AST and
reimplementing Python's own name resolution by hand — a strictly worse,
more fragile option this item declines outright rather than half-building.
Whoever eventually sandboxes a `call` node's real execution (`§2.3`,
after EXECUTION) should re-examine whether this validation-time import
still needs the same trust boundary once untrusted plugins exist; that is
explicitly not answered here.

**A second, smaller tension**: adding `jsonschema` is this project's first
declared runtime dependency, a real, visible change to `pyproject.toml`'s
`dependencies = []` — a line several existing docstrings in this codebase
point to as a current fact about the project (`_parse_skill_md`'s own
docstring: "this project has zero runtime dependencies today"). That
docstring becomes stale the moment this item lands and should be corrected
in the same diff, not left to drift — it is not a closed work item's own
artifact (it is a code comment, not `docs/tasks/`), so nothing in
CLAUDE.md's rule against editing closed artifacts protects it from being
brought current.
