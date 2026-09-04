# Spec: A closed, single-skill sub-task can be spawned and reports back cleanly

Intent: docs/tasks/C9-child-conversation/intent.md

## Requirements

1. A caller can start a fresh sub-conversation that runs exactly one named
   skill against one input, isolated from the parent's own state. (Intent
   §Proposed outcome.)
2. The caller chooses the sub-task's tool list at spawn time; the sub-task
   can never invoke a tool outside that list, even if the model asks for
   one. (Intent §Proposed outcome, §Constraints.)
3. The caller chooses the sub-task's iteration budget at spawn time; when
   none is given, a config-provided default applies. No further ceiling is
   imposed on a value the caller does supply. (Intent §Constraints,
   §Changed during planning.)
4. The sub-task inherits the parent's remaining wall-clock allotment
   unchanged, so it can never outlive the parent's own run. (Blueprint
   §4.5; intent did not revisit this axis.)
5. The caller can choose a different model for the sub-task than the one
   the parent is using; when none is given, the sub-task inherits the
   parent's model. (Interview: per-spawn model control, confirmed
   alongside budget and tools.)
6. The sub-task's nesting depth is derived from its parent's, and a spawn
   that would exceed a configured maximum depth fails before anything is
   spent. (Blueprint §4.5; carried forward as the one bound the interview
   did not ask to remove — see §Concerns.)
7. The sub-task reports back exactly what it produced. Nothing about the
   result is validated, retried, or scored by this mechanism. (Intent
   §Constraints, "not this mechanism's problem to catch.")
8. Nothing the sub-task did is folded into the parent's own transcript or
   result automatically. The sub-task's full record still exists as an
   ordinary value and can be inspected by whoever holds it. (Intent
   §Proposed outcome, §Constraints.)
9. A sub-task's skill instructions are loaded from disk by a caller-given
   name, resolved against one configured root, and the load fails loudly
   if the file is missing or its declared name doesn't match what was
   asked for. (Intent §Affected users and systems; resolution shape fixed
   in the design-stage clarification below.)
10. Building the orchestration engine that will eventually construct these
    spawns is out of scope; only the primitive it will call. (Intent
    §Constraints, explicit.)

## Design

A child conversation is assembled by reusing every mechanism this block
already built — `filter_surface`, `turn_prompt_hash`, `take_turn` — plus
four small additions: a depth guard, a per-parent sequence, a skill
loader, and the `ChildSpec`/`run_child` boundary itself. The subsections
below work through where it lives, what the reference corpus contributed
and where this deliberately departs from it, the new types, how a skill
name resolves to a file, why depth is derived rather than stored, and why
tool restriction needed no new enforcement.

### Where this lives

Continuing in `src/sadana/conversation.py`, under a new
`# ── CONV-07: child conversation ──` section, the same convention every
prior work item in this block used (C2 through C8 all landed in this one
file rather than the package-of-modules layout the blueprint originally
sketched in §3). See §Concerns for the file-size cost of that convention
continuing here rather than being revisited.

Tests extend `tests/unit/test_conversation.py`, per `testing-conventions`
(one file per module under test; this module already has one).

### Reference corpus: what's adopted, what's declined

`tools/delegate_tool.py` is the analogous hermes mechanism (blueprint
§2.2, "Subagent as a fresh agent with a focused prompt").

- **Adopted**: the shape — a child is a genuinely new agent/conversation
  value, with its own budget, an explicit (not inherited-by-default)
  toolset, and a depth derived from the parent rather than declared by the
  caller (`_build_child_agent`, `:1783-1790`).
- **Adopted, narrowed**: hermes derives a *role* from depth and folds it
  into the child's prompt. Sadana has no role concept and tools are
  already explicit per spawn, so only the depth-derivation half
  transplants; there is nothing for a "role" to add here.
- **Declined, with a specific reason**: `_build_child_system_prompt`
  (`:1230`) interpolates the task text itself into the child's system
  prompt alongside the framing boilerplate. This work item does not: the
  task (`spec.input`) is delivered exactly once, as the child's first
  ordinary user turn (via `take_turn`'s existing `user_input` parameter),
  and the system prompt carries only skill instructions plus a fixed,
  input-independent framing block. Concretely wrong with the hermes
  approach for this codebase: it makes the system prompt — the one thing
  this block enforces byte-stability on — depend on per-spawn data, for
  no benefit, since the same content already has a channel (the first
  user turn). Two children running the same skill now also produce
  identical system prompts, which nothing here currently depends on but
  costs nothing to keep true.
- **Declined, already ruled out at blueprint level**: the ~90-argument
  `init_agent` / mutable-attribute coupling (blueprint §2.3, "the god
  object"). Not revisited here; still the reason this module passes
  everything a call needs as explicit arguments.

### Types and functions introduced

```python
@dataclass(frozen=True)
class SkillRef:
    plugin: str
    skill: str


class SkillLoadError(Exception):
    """SKILL.md is missing, malformed, or its declared name doesn't match
    what was asked for."""


class ChildDepthExceeded(Exception):
    """A spawn's derived depth exceeds the configured maximum. Raised
    before any budget, transcript, or model-call state is touched — same
    posture as PromptDriftError: a structural refusal, not a run outcome,
    so it is never an ExitReason."""


@dataclass(frozen=True)
class ChildSpec:
    node_name: str
    skill: SkillRef
    input: str
    tools: frozenset[str]
    budget: IterationBudget | None = None
    model: str | None = None


# What run_child returns for its single turn. Not a new type: TurnResult
# already carries the child's own key (turn_key.conversation), and this
# work item has no field to add beyond that. See Rejected alternatives.
ChildResult = TurnResult


def load_skill(ref: SkillRef) -> str: ...
    # Resolves ref against the configured plugins root, reads
    # <root>/<plugin>/skills/<skill>/SKILL.md, validates the frontmatter's
    # `name` matches `ref.skill` and `description` is present and
    # <= 1024 chars, and returns the body. Raises SkillLoadError on any
    # of those failing. No YAML dependency: the frontmatter this work
    # item reads is two flat string fields, and this project has zero
    # runtime dependencies today (pyproject.toml) — a hand-rolled
    # `---`-delimited parser is a dozen lines, not a new dependency.


def child_iteration_budget_from_config() -> IterationBudget: ...
    # Reads SADANA_CONVERSATION_CHILD_MAX_ITERATIONS, default 20
    # (blueprint §5.7). Same negative-value ValueError posture as
    # iteration_budget_from_config.


def child_max_depth_from_config() -> int: ...
    # Reads SADANA_CONVERSATION_CHILD_MAX_DEPTH, default 2
    # (blueprint §5.7). Same negative-value ValueError posture.


async def run_child(
    parent: Conversation,
    spec: ChildSpec,
    *,
    stable_prompt: str,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[str]],
    compress: Callable[[tuple[Message, ...], str], Awaitable[str | None]],
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    now: float,
) -> tuple[ChildResult, Conversation, Conversation]:
    """Returns (the child's turn result, the child's own updated
    Conversation, the parent's updated Conversation — next_child_seq
    incremented, nothing else changed). Three values with three different
    lifetimes, kept separate rather than folded into one dataclass — the
    same call run_turn's own docstring already made."""
```

`stable_prompt` is a plain caller-supplied string, the same posture
`create_conversation`'s `system_message` already has: this module doesn't
discover it from `parent.template_name` (Conversation deliberately holds
only the name, never the template — C8's own design), so whoever calls
`run_child` supplies it, same as it already supplies `provider`/`model`/
`dispatch`/`compress`/`persist`/`now` to `take_turn`.

### Resolving a skill by name (the intent's open question, now settled)

Design-stage clarification with the requester: `SkillRef` is symbolic
(`plugin`, `skill`) rather than a filesystem `Path`, because a plugin's
actual on-disk location can change independently of anything holding a
reference to it — a re-install or version bump from the future
marketplace the requester described. That is exactly the case CLAUDE.md's
naming rule names: *"a value should hold another long-lived value's name,
never a live reference to it, whenever something else might change
independently of what already holds the reference."* A bare `Path` would
be a live reference; `SkillRef` is a name.

Resolving that name today costs one function and one config value, not a
registry:

```python
def _plugins_root() -> Path:
    return config.env_path("SADANA_PLUGINS_DIR", default=config.get_paths().state_dir / "plugins")


def _skill_path(ref: SkillRef) -> Path:
    return _plugins_root() / ref.plugin / "skills" / ref.skill
```

`skills/<skill-name>/SKILL.md` is the layout the requester already
committed to for the future plugin repo shape, so this doesn't invent a
convention — it uses the one that already exists, one level early. When a
real installer lands, only what populates `_plugins_root()` changes;
`SkillRef`, `load_skill`, and every caller of either stay untouched.

`SADANA_PLUGINS_DIR` is deliberately not `SADANA_CONVERSATION_PLUGINS_DIR`
despite `config.py`'s own `SADANA_<BLOCK>_<FIELD>` convention note: the
value's real owner is a PLUGINS block that doesn't exist yet, and
prefixing it as conversation-owned would just mean renaming it out from
under that block later. `config.py` itself says nothing enforces the
convention in code.

Defaulting the root under `state_dir` gets test isolation for free —
`testing-conventions`' autouse fixture already redirects the state
directory to a tmp path for every test, so a test that writes a fixture
skill under that tmp root needs no new fixture of its own. It also
conflates two concepts that aren't really the same thing ("runtime
state" and "installed plugin content") — flagged in §Concerns rather than
fixed here, since giving plugins their own config concept is the future
PLUGINS block's decision to make, and today's default is one function
call, cheap to change without touching any caller.

### Depth: derived, not stored

`Conversation` gets no new `depth` field. A child's key is always minted
by `run_child` itself (`_child_key`, below) — the caller never supplies
one — so the key's shape is fully under this module's control, and depth
is recovered by counting `"child"` path segments in it:

```python
def _child_key(parent: ConversationKey, node_name: str, seq: int) -> ConversationKey:
    return f"{parent}/child/{node_name}/{seq}"


def _conversation_depth(key: ConversationKey) -> int:
    return sum(1 for segment in key.split("/") if segment == "child")
```

This is the same "the lineage is in the name" posture blueprint §4.1
already established for the key format itself — a depth counter would be
redundant state that could drift from what the key already encodes.
Caveat, not enforced: a `node_name` that is itself exactly `"child"`
would be miscounted by one. `node_name` is caller-chosen (a future DAG's
job) and short; narrower than the substring-counting alternative, and not
worth a runtime check for a value nothing external ever supplies today.

### The per-parent sequence

`seq` in `_child_key` comes from a new `Conversation.next_child_seq: int
= 0` field — added with a default so every existing keyword-based
construction of `Conversation` (C8's own tests included) is unaffected.
Same shape as the existing `next_turn_seq`: one monotonic counter,
incremented by `replace()`, never a dict keyed by `node_name` — a single
counter already gives every child spawned from one parent a distinct key
regardless of which node asked for it, and nothing needs the count of
*that specific node's* children.

### Assembling the child

```python
async def run_child(parent, spec, *, stable_prompt, provider, model,
                     dispatch, compress, persist=_noop_persist, now):
    depth = _conversation_depth(parent.key) + 1
    if depth > child_max_depth_from_config():
        raise ChildDepthExceeded(f"depth {depth} exceeds configured maximum")

    seq = parent.next_child_seq
    updated_parent = replace(parent, next_child_seq=seq + 1)
    child_key = _child_key(parent.key, spec.node_name, seq)

    skill_body = load_skill(spec.skill)
    system_prompt = "\n\n".join(part for part in (stable_prompt, skill_body, _CHILD_TASK_FRAMING) if part)
    tool_surface = filter_surface(parent.tool_surface, spec.tools)
    prompt_sha256 = turn_prompt_hash(system_prompt, tool_surface)

    child = Conversation(
        key=child_key,
        template_name=parent.template_name,
        system_prompt=system_prompt,
        prompt_sha256=prompt_sha256,
        prompt_epoch=0,
        tool_surface=tool_surface,
        messages=(),
        next_turn_seq=0,
        iteration_budget=spec.budget if spec.budget is not None else child_iteration_budget_from_config(),
        wall_clock_budget=parent.wall_clock_budget,
        next_child_seq=0,
    )

    result, updated_child = await take_turn(
        child,
        user_input=spec.input,
        provider=provider,
        model=spec.model if spec.model is not None else model,
        dispatch=dispatch,
        compress=compress,
        persist=persist,
        now=now,
    )
    return result, updated_child, updated_parent
```

Everything past the depth check reuses existing machinery wholesale:
`filter_surface` (C3), `turn_prompt_hash` (C7), `take_turn` (C8) for the
turn itself. `run_child`'s own new logic is the depth guard, the sequence
bump, the key, and the prompt/tool-surface assembly — nothing here
reimplements any part of the turn loop.

`_CHILD_TASK_FRAMING` is a fixed, `spec.input`-independent module
constant, under 20 lines per blueprint §5.4:

```python
_CHILD_TASK_FRAMING = (
    "You are a focused subagent, launched to complete one bounded task "
    "and then stop. Make a reasonable choice and proceed rather than "
    "asking a clarifying question. When you are done, report plainly: "
    "what you did, what you found, what (if anything) changed, and any "
    "issues you ran into. Nothing else reads your intermediate steps, "
    "so make your final answer stand on its own."
)
```

### Why tool restriction needs no new enforcement

`spec.tools` only ever reaches the model as `filter_surface(parent.
tool_surface, spec.tools)` — the child's own `tool_surface`. `run_turn`
(C7) already partitions every tool call the model makes into `valid`
(name present in `tool_surface`) and `invalid`, and only ever calls
`dispatch` for `valid` calls. A name outside `spec.tools` is therefore
never offered to the model and, even if a provider hallucinated one
anyway, would hit `run_turn`'s existing `INVALID_TOOL_CALLS` handling —
not a new code path. Considered and rejected: wrapping `dispatch` to
explicitly reject a name outside `spec.tools` before calling through —
this would enforce the identical invariant a second time, on the
model-facing side, for no scenario the first enforcement doesn't already
catch.

## Interface

Errors this work item introduces: `SkillLoadError`, `ChildDepthExceeded`
— both raised before any budget, model-call, or transcript state changes,
matching `PromptDriftError`'s existing posture of "a bug/refusal, not a
run outcome."

Every other input/output crossing `run_child`'s boundary is a type this
block already defined: `Conversation`, `TurnResult` (aliased as
`ChildResult`), `IterationBudget`, `WallClockBudget`, `ToolSurface`.

## Acceptance criteria

- [ ] `run_child` returns a `ChildResult` whose `turn_key.conversation`
      embeds the parent's key and the given `node_name`
      (`f"{parent}/child/{node_name}/{n}"`).
- [ ] Two `run_child` calls against the same parent produce two distinct
      child keys, differing only in the trailing sequence number.
- [ ] A child's `tool_surface` contains only the names in `spec.tools`,
      even when `parent.tool_surface` has more; a model call that asks
      for a tool outside that set ends the child's turn with
      `ExitReason.INVALID_TOOL_CALLS`, not a dispatch call.
- [ ] `spec.budget`, when given, is used verbatim as the child's
      `IterationBudget` — no clamping against
      `SADANA_CONVERSATION_CHILD_MAX_ITERATIONS` even when the supplied
      value exceeds it.
- [ ] `spec.budget=None` yields a child `IterationBudget` built from
      `child_iteration_budget_from_config()`.
- [ ] The child's `wall_clock_budget` is the identical `WallClockBudget`
      value the parent had (or `None` if the parent had none) — not
      recomputed.
- [ ] `spec.model`, when given, is the model `run_child` passes to
      `take_turn` for the child; `spec.model=None` passes the caller's
      own `model` argument through unchanged.
- [ ] A `run_child` call whose derived depth exceeds
      `child_max_depth_from_config()` raises `ChildDepthExceeded` and
      calls neither `dispatch` nor the provider port.
- [ ] `run_child` never appends to, or otherwise mutates, `parent.
      messages`; the only difference between `parent` and the returned
      updated parent is `next_child_seq`.
- [ ] `load_skill` returns a `SKILL.md`'s body when its frontmatter's
      `name` matches the requested skill and its `description` is
      present and ≤ 1024 characters; raises `SkillLoadError` when the
      file is absent, the frontmatter is missing or malformed, the name
      doesn't match, or the description is absent or too long.
- [ ] Two children spawned with the same `spec.skill` and the same
      `stable_prompt` produce byte-identical `system_prompt` values
      regardless of `spec.input` — the byte-stability contract (C8's own
      `turn_prompt_hash`) verified for a child the same way §Acceptance
      already verified it for a top-level `Conversation`.
- [ ] Every test lives in `tests/unit/test_conversation.py`, uses
      `monkeypatch.setenv`/`delenv` for every config-reading assertion,
      writes fixture `SKILL.md` files only under `tmp_path` (never the
      real state directory), and reads no `.py` source text.

## Non-goals

- `skill.read_reference` and any references/assets progressive-disclosure
  tool (blueprint §5.4) — cut from this work item by the requester's own
  confirmed floor during the intent interview; add it, as its own work
  item, only once a skill that actually needs it exists.
- Warning or otherwise validating a `SKILL.md` body's size (blueprint §9's
  named risk: a large body eats a child's context). Also cut per the
  confirmed floor; the risk is real and stays uncaught until a follow-up
  item adds it — see §Concerns.
- A config-enforced ceiling on a caller-supplied `spec.budget` — considered
  and explicitly rejected during the intent interview; the budget is
  already a self-exhausting resource, and a second cap would guard
  against nothing an unbounded value doesn't already bound itself against
  once it starts being consumed.
- Provider override per child — blueprint §5.4/§8.6 name only a model
  override, resolved through the existing provider port; not extended
  here.
- Any plugin install/marketplace/webhook machinery. This work item defines
  only the naming convention (`SkillRef`) and its resolution against one
  configured root; nothing here fetches, verifies, or installs a plugin.
- Persisting a child's `Conversation` anywhere durable. `run_child`
  returns the value; keeping it around for later lookup, if a caller
  wants that, is the caller's job until CONV-08's store exists.

## Open questions

- Whether `SADANA_PLUGINS_DIR`'s default should keep living under
  `state_dir`, or get a config concept of its own once a PLUGINS block
  exists to own it. Deferred to that block; today's default has no
  caller depending on its specific value, and moving it later is a
  one-function change.

## Rejected alternatives

- **`SkillRef` as a filesystem `Path`.** The original design-stage
  proposal; superseded once the requester described plugins as
  downloaded/reinstalled from a future marketplace — a path is a live
  reference to something that can move independently of whatever holds
  it, exactly the case CLAUDE.md's naming rule addresses. See §Design.
- **A config-enforced ceiling on `spec.budget`.** Raised during the intent
  interview as a candidate safety rail; rejected by the requester on the
  grounds that `IterationBudget` already returns nothing once exhausted —
  a second cap on top would be catching a scenario the budget mechanism
  already catches, i.e. adding a step with no scenario left to cover. See
  intent §Changed during planning.
- **Embedding `spec.input` in the child's system prompt** (hermes's
  `_build_child_system_prompt` shape). Rejected in favor of delivering it
  once, through `take_turn`'s existing `user_input` — same content
  through the existing first-turn channel rather than a second, prompt-
  embedded copy. See §Design's reference-corpus discussion.
- **A `ChildResult` dataclass distinct from `TurnResult`.** No field it
  would need doesn't already exist on `TurnResult` (`turn_key.conversation`
  is the child's key); a new type would be a second name for the same
  three values with nothing to add.
- **Storing `depth` as a `Conversation` field.** Rejected in favor of
  deriving it from the key, which `run_child` already fully controls the
  shape of — an explicit field would be state that could only ever agree
  with, never disagree usefully from, what the key already encodes.
- **Wrapping `dispatch` to re-check `spec.tools`.** Rejected: `run_turn`'s
  existing valid/invalid tool-call partition already makes a call outside
  the child's surface unreachable from the model side; a second check
  would enforce the same invariant twice.
- **A directory-symlink or copy-on-spawn skill resolution** (materializing
  a skill's files somewhere per-child before reading them). Rejected:
  nothing about a read-only `SKILL.md` body needs per-child isolation;
  `load_skill` just reads the file where it already lives.

## Concerns

- **The no-ceiling budget decision is the one place a reviewer should look
  hardest.** It's correct today, when every caller of `run_child` is this
  project's own tests and evidence script. It stops being obviously
  correct the moment an untrusted, marketplace-downloaded plugin can
  choose its own child's budget — at that point "the budget is
  self-bounding" is true per-spawn but says nothing about aggregate cost
  across many spawns. Nothing in this work item's scope needs to solve
  that yet (there is no marketplace), but whoever designs plugin
  installation should revisit this specific decision rather than assume
  it, and it should be revisited then, not assumed to still hold.
- **`conversation.py` keeps growing past blueprint §3's own suggested
  400-line ceiling per file** (already exceeded by C4 through C8; this
  work item adds roughly 150-200 more lines on top of the current 1068).
  Continuing the established single-file convention here is a smaller bet
  than unilaterally restructuring six already-closed work items' code on
  this item's back — but it is a cost, not a non-issue, and a dedicated
  maintain-stage item to split the module is a reasonable thing for the
  requester to schedule independent of this one.
- **Cutting SKILL.md size validation** (Non-goals) leaves blueprint §9's
  named risk — a large skill body eating a child's context — genuinely
  uncaught, not merely deferred in name only. This was a deliberate,
  requester-confirmed scope cut, not an oversight, but it is worth being
  explicit that "confirmed floor" and "no known risk" are different
  things here.
- `testing-conventions` is the only applicable policy skill; no
  `project-structure` or `reference-lookup` skill exists in this repo to
  check against (same as every prior work item in this block noted). No
  security, brand, or UX policy applies to an in-process value-passing
  mechanism with no network, filesystem-outside-fixtures, or user-facing
  surface of its own.
