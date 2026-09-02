# Spec: CONFIG as a contract, not a module

Intent: docs/tasks/B1-config-contract/intent.md

## Requirements

1. sadana-harness exposes exactly one way to obtain a configuration value: a
   single importable entry point every block calls. *(Proposed outcome: "one
   defined, documented way to ask for one".)*
2. Every value is resolved through one fixed, ordered precedence chain,
   implemented in exactly one place. *(Proposed outcome: "one defined way to
   change where that value comes from... without editing every place the
   value is used".)*
3. The mechanism that resolves a value works identically regardless of its
   type, so a later block can add a toggle by adding a field, not by
   inventing a second resolution path. *(Proposed outcome, toggle clause.)*
4. The values a caller can ask for are typed and named in one declaration
   per block, so a caller does not need to know how a value is stored or
   derived. *(Proposed outcome: "without needing to know how configuration
   is stored or loaded internally".)*
5. CONFIG imports nothing from any other part of sadana-harness.
   *(Constraints: standalone — no other block exists yet.)*
6. Nothing here stores, transmits, or rotates secrets or credentials.
   *(Constraints: secrets out of scope.)*
7. The contract is documented in the module itself, not only in this spec,
   so a later block author finds it by reading the code they're about to
   call. *(Proposed outcome: "can find and follow that way without having to
   guess".)*
8. Adding configuration for the Nth block never requires modifying
   `sadana/config.py` or growing a shared object — it happens entirely in a
   file that block owns. *(Proposed outcome, added on amendment: "the way of
   asking for a value has to hold the same way at twenty blocks as it does
   at one".)*

## Design

**Policies applied.** `testing-conventions` (tier, naming, isolation — see
Acceptance criteria and the caching note). There is no `project-structure`
or `reference-lookup` skill in this repo; CLAUDE.md's own "Layout and
ownership" section stands in for the first (`src/sadana/` is the package —
this adds one module, `src/sadana/config.py`, no subpackage). No security,
brand, or UX skill applies to a backend value-resolution module.

**Amendment note.** This spec was rewritten after a first version shipped a
single growing `Config` dataclass and the risk of that approach was raised:
at repo scale it reproduces, one level down, the exact failure this work
item exists to prevent. The reference corpus already shows what that failure
looks like — `hermes_constants.py` is 1,878 lines and almost entirely
functions because its own docstring reasoning was "import-safe... can be
imported from anywhere without risk" (1-4): a single "anywhere-safe" file
became the place everything got dumped as the codebase grew. A single
growing `Config` object is the same reasoning, and would end the same way.
This version distributes ownership instead: see "The contract itself"
below.

**What the reference corpus showed** (unchanged from the first draft — a
fork read of the 19 CONFIG-block files and a grep over the wider hermes
tree; cited by file:line):

- No consistent access shape. `hermes_cli/config.py` returns a raw `dict`
  from `load_config()` (3723), indexed by string key everywhere it's used.
  `gateway/config.py` returns a typed `GatewayConfig` from
  `load_gateway_config()` (774); callers import specific classes by name
  (`Platform`, `PlatformConfig`, `StreamingConfig` — 325/647/1383).
  `hermes_constants.py` is almost entirely functions imported one at a time
  (`get_hermes_home`, 114).
- The 2,319-edge problem is rooted at the *name* level: ~50+ distinct
  exported names, each an independent breaking-change surface, often
  imported function-locally to dodge a circular import hermes already has
  (`managed_scope.py:153` imports back from `hermes_cli.config` lazily "to
  avoid an import cycle (config imports managed_scope)").
- One real precedent for "swap a value's source without touching callers":
  `env_loader.py`'s `agent.secret_sources.registry` (203, 273, 676, 748).
  Another: `display_config.py`'s explicit 4-tier precedence chain
  (docstring, 3-11). Both are precedent for *resolution logic living in one
  place*, not for centralizing every block's field list in one place — that
  distinction is what this amendment acts on.
- Precedence-merge logic is implemented twice in hermes:
  `managed_scope.py:151-178` admits (153) it "mirrors
  `hermes_cli.config._load_config_impl`'s managed merge **exactly**."
  Declined either way — one resolution function, reused, not reimplemented.

**Guideline 2 — reduce the number of bets.** The first draft of this spec
judged sadana-harness to have no plugin seam yet and declined to build
toward one. That judgment changes here: the user has now explicitly named a
real, near-certain, repeated future need — every one of the ~21 blocks this
project will eventually contain will need configuration — as in scope for
this work item, not a hypothetical. That's the difference between
speculative infrastructure and a named, committed requirement. But the
answer is not "build a registry to hold everyone's future bets" — it's
**"need no registry at all."** Each block owning its own config file, built
from a handful of shared stdlib-only functions, is strictly fewer bets than
a registry: no registration order to get right, no composed object whose
shape depends on which blocks happen to be imported yet, nothing to
register. `sadana/config.py` never needs to change again after this work
item ships — every future block's config need is met by *reuse*, the
cheapest possible instance of Guideline 2.

**Guideline 3 — least step-cost.** The scenario: a new block needs a
config value, and getting it must not touch CONFIG's own file or force a
central object to grow. The move is still *add a step* — but the step is
added once, here, as four small reusable functions. Every subsequent
instance of the scenario (block 2, block 3, ... block 21 needing
configuration) is caught by *reusing an existing step*, not by adding a new
one. That is cheaper than the alternative registry design, which would add
a step (register) at every block's boundary, forever.

**Guideline 4 — minimise mutable state.** Zero stored state, same as the
first draft, now more clearly so: no registry dict, no registration-order
dependency, nothing composed at import time. `get_paths()` still builds a
fresh, immutable `Paths` from `os.environ` on every call, and the four
resolution primitives are pure functions of `(name, default, environment)`
— nothing cached, nothing shared, nothing that can go stale between a test
and the one after it.

**The contract itself.**

```
src/sadana/config.py

@dataclass(frozen=True)
class Paths:
    state_dir: Path
    config_dir: Path

def get_paths() -> Paths: ...

def env(name: str, default: str) -> str: ...
def env_bool(name: str, default: bool) -> bool: ...
def env_int(name: str, default: int) -> int: ...
def env_path(name: str, default: Path) -> Path: ...
```

`Paths` covers the only two values that are genuinely global and ownerless
— `state_dir` and `config_dir`, both already assumed by
`tests/conftest.py:20-22` — and this is the last field this file will ever
need, by design: nothing else belongs to "everyone," so nothing else
belongs here.

Every other value, for every future block, is obtained by calling `env` /
`env_bool` / `env_int` / `env_path` from that block's *own* module, e.g. (not
part of this work item — illustrative only) `src/sadana/model_access/config.py`
defining its own `ModelAccessConfig` from `env_int("SADANA_MODEL_ACCESS_TIMEOUT_S", 30)`.
`sadana/config.py` is never touched to add that field. The four functions
implement the resolution chain once — env var if set, else the given
default, raising `ValueError` if a value is set but fails to parse as its
declared type — and every block reuses that same chain instead of writing
its own `os.environ.get(...)` parsing, which is exactly how hermes ended up
with per-file, inconsistent coercion helpers (`gateway/config.py`'s own
`_coerce_bool/_coerce_int`, 32-320, duplicate what `hermes_cli/config.py`
does its own way elsewhere).

**Naming convention** (documented, not code-enforced — see Concerns): a
block's env vars are named `SADANA_<BLOCK>_<FIELD>`, uppercase, e.g.
`SADANA_MODEL_ACCESS_TIMEOUT_S`. This keeps a flat environment-variable
namespace collision-free as blocks accumulate without needing any
hierarchical machinery.

## Interface

**Public names — exactly five.**

- `Paths` — frozen dataclass, fields `state_dir: Path`, `config_dir: Path`.
  Immutable.
- `get_paths() -> Paths` — no arguments, reads `os.environ` only, cannot
  raise (every field has a built-in default).
- `env(name: str, default: str) -> str`
- `env_bool(name: str, default: bool) -> bool`
- `env_int(name: str, default: int) -> int`
- `env_path(name: str, default: Path) -> Path`

  Each of the four: returns `default` if `name` is unset in the
  environment; returns the parsed value if set and parseable. `env_bool` and
  `env_int` raise `ValueError` if set but not parseable as their declared
  type (a bad `SADANA_FOO_TIMEOUT_S=notanumber` should fail loudly at the
  one call site that reads it, not silently fall back). `env` and
  `env_path` never raise — any string is a valid string, and
  `pathlib.Path` accepts essentially any string, so neither has an invalid
  case to reject.

Nothing else is exported. `sadana.config` imports only the standard
library.

## Acceptance criteria

- [ ] `src/sadana/config.py` exists and exports exactly `Paths`,
      `get_paths`, `env`, `env_bool`, `env_int`, `env_path`.
- [ ] `Paths` is frozen; setting an attribute on an instance raises.
- [ ] `get_paths()` builds values from the current environment on every
      call, no caching — two calls straddling a changed environment
      variable return different values.
- [ ] `SADANA_STATE_DIR` set → `get_paths().state_dir` equals it exactly;
      unset → falls back to `$XDG_STATE_HOME/sadana`, further unset →
      `~/.local/state/sadana`. `config_dir` follows the same shape from
      `XDG_CONFIG_HOME`.
- [ ] `env_bool`/`env_int`: set-and-valid returns the parsed value; unset
      returns the given default; set-and-invalid raises `ValueError`.
      `env_path`: set-and-valid returns the parsed value; unset returns the
      given default (no invalid case — `pathlib.Path` accepts essentially
      any string).
- [ ] `sadana.config` has no import from any other module under
      `src/sadana/`.
- [ ] `tests/unit/test_config.py` exists (testing-conventions naming) and
      asserts the precedence and parse/raise behavior above as invariants,
      not literal-path snapshots.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Reading or writing an on-disk config file. `config_dir` is exposed as a
  resolved path for a later block to use; no file format is defined because
  nothing needs one yet. The primitives' signatures leave room to add a
  file-backed tier later inside `env`/`env_bool`/etc. without changing any
  block's call site.
- Secrets or credential storage, retrieval, or rotation.
- Config schema versioning or migrations — nothing has shipped under this
  contract yet to migrate from.
- A central registry, plugin system, or any other mechanism for CONFIG to
  learn what blocks exist. Deliberately not needed — see Guideline 2 above.
- Any actual block-owned config file (e.g. a real `model_access/config.py`).
  Only the shared primitives and the two bootstrap fields ship in this work
  item; the first real consumer is a future block's own work item.
- Enforcing the `SADANA_<BLOCK>_<FIELD>` naming convention or a
  no-cross-block-config-import rule in tooling. Documented, not linted (see
  Concerns).
- Interpreting config content (timeouts, provider fallback order, toolset
  validity). Consumer logic layered on top of a value, owned by whichever
  future block needs it.

## Rejected alternatives

- **One shared, growing `Config` object** (this spec's own first draft) —
  superseded on amendment. It scales the way `hermes_constants.py` did: a
  single "safe to import from anywhere" file becomes the place every block
  adds a field, until it is its own 1,000+ line sprawl with no per-block
  ownership boundary.
- **Central registry** (blocks call `register_section("model_access", ...)`
  into a composed root `Config`) — considered and declined by the user
  directly. It's strictly more than distributed primitives: a registry dict
  is stored state with a lifetime and an import-order dependency (Guideline
  4), for the same outcome distributed primitives get with zero state.
- **Module-level singleton, direct attribute import**
  (`from sadana.config import CONFIG; CONFIG.state_dir`) — declined, same
  reasoning as the growing-object rejection above: it's the pattern that
  produced `hermes_constants.py`'s sprawl in the first place.
- **Dict-based config**, `hermes_cli/config.py`'s shape — declined. This
  project's `disallow_untyped_defs = true` mypy gate makes untyped
  string-keyed access strictly worse for no savings.
- **mtime-cached resolution** — declined. Nothing is read from disk in this
  work item; caching a value cheaper to recompute than to check for
  staleness is stored state with no benefit.
- **Following the CSV's CONFIG-block clustering as-is** (also pulling in
  `hermes_logging.py`, `hermes_time.py`, `utils.py`,
  `hermes_cli/__init__.py`) — declined; the "anywhere-importable" reasoning
  that produced `hermes_constants.py`'s sprawl most likely swept those in
  too. They aren't configuration; they belong to whichever future block
  owns logging, time, or general utilities.

## Concerns

Nothing in this repo enforces, in tooling, that a block's own config module
stays free of imports from another block's config module, or that two
blocks don't pick colliding env var names. Both rely on the documented
`SADANA_<BLOCK>_<FIELD>` convention and on code review at each future
block's own deploy stage, not on `make verify`. This is the same category of
gap as the standalone-import check on CONFIG itself (no `project-structure`
lint exists in this repo) — worth a lint rule once there are enough blocks
for a violation to be likely rather than hypothetical, not now.

Second: `env_bool` and `env_int` have no call site inside this work item —
`Paths` only needs `env_path`. They ship anyway because the user explicitly
named repo-wide scaling, across every future block, as in scope for this
amendment; that is a deliberate, on-the-record exception to Guideline 2
("no field with no consumer"), not an oversight. A reviewer who thinks that
exception shouldn't be granted should say so — the alternative is shipping
`env`/`env_path` now and adding `env_bool`/`env_int` at whichever future
work item first needs one, at the same one-function cost either way.
