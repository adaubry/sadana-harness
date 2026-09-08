# sadana-harness

Version 1.01 (03/09/2026)

An agent runtime built from scratch. `../hermes-agent` is a **reference corpus.**
Refer to our methodology for learning from the reference.
Nothing here imports from it, we use its patterns and logic to speed up our build.

WSL only. Python lives in `.venv`; `make` puts it on PATH for you, so there is nothing to activate.

## Do not

- Write code before `plan.md` exists and the user has approved it.
- Approve your own work or merge on your own judgement. That decision is the
  user's, and it is the only part of this process that cannot be recovered
  afterwards.
- Edit a closed work item's artifacts to match what the code became.
- Verbatim copy Hermes code without auditing it against our paradigm and our project.
- Paginate tools that load content the agent must read fully: Models will read page 1 and skip the rest.” Pagination on an instruction is an invitation to ignore it.
- Infer process identity from argv substrings
- Read source code in a test

## Please do

- When you simplify a procedure, name the step that is still load-bearing and say why.
- Use names, not pointers for anything long-lived: Anything long-lived a user returns to should be addressed by a unique natural key with a database constraint behind it
- Apply the same rule in memory, before a database exists: a value should hold another long-lived value's name, never a live reference to it, whenever something else might change independently of what already holds the reference.
- Use .env for secrets (API keys, tokens, passwords, etc...)
- Use config for behaviours (Timeouts, thresholds, feature flags, display preferences go in the config file; bridge internally to an env var if a mechanism needs one, but the user-facing setting is the config key)
- Know which config loader you are inside
- Isolate by process, and delete the state-reset fixtures when testing
- Say exactly what each async operation survives
- compose tool descriptions that reference other tools at definition-build time from the resolved set, ensuring no cross-reference is ever a literal
- When it comes to the system prompt, enforce a state contract that makes the principle "the system prompt is byte-stable for the life of a conversation" directly checkable, maintaining compression as the single named exception and using deferred invalidation as the default for any action that mutates prompt state
- Prove a block's first real external round trip with a standalone script outside `make test`; paste its output as Deploy-stage Evidence rather than relaxing testing-conventions' network ban in the unit suite
- A conversation's message history is mutated only through conversation.append/conversation.repair/conversation.compact — never by direct list or tuple mutation elsewhere
- Model a resource that can be consumed or exhausted (a budget, an allowance) as an immutable value with a consume() function returning a new value or None — never a mutable counter guarded by a lock
- A resource already modeled as a self-exhausting consume() value needs no additional external ceiling on a caller-chosen amount — its own exhaustion is the enforcement. Add a ceiling only to catch a genuinely different scenario (e.g. unbounded recursion depth), never to double-guard the one the resource already bounds.
- A module that touches real I/O (disk, network, the clock) is its own file, separate from a block's pure-function module, regardless of line count.
- Revisit `conversation_store.py`'s single connection the moment either signal actually shows up, not before — it is right-sized only while it has one writer at a time. (1) A second long-lived in-process caller can open it as a writer concurrently with another — a spawned child (run_child) still writing while its parent holds a connection, a background/scheduled task writing alongside a live turn — that is the exact condition that made hermes-agent build a refcounted per-path connection registry, after 11+ production incidents of independent writer connections racing one SQLite WAL file; a "database is locked" error or a checkpoint race in our own logs is that condition arriving for real, not a flake to retry past. (2) A second storage backend is actually being built, not hypothesized — only then does a seam belong at construction, as a Protocol, the way hermes's other nine provider subsystems (TTS, transcription, image/video gen, web search, browser, memory, terminal env, context engine, computer-use) do it, never retrofitted onto the class that already won.
- Provider-specific wire-format knowledge — what a given transport's request shape accepts or silently drops — lives in MODEL-ACCESS, never in CONTEXT or CONVERSATION.
- A retry loop over a provider's Retry outcome lives once, in MODEL-ACCESS's own resolve(), never duplicated per caller.
- A plugin's outcome crosses back to its caller only as a returned DagResult — never a raised exception for an expected outcome, a string convention, or any other side channel.
- A Task's grade() reads DagResult/NodeTrace structure to judge plugin behavior — never a substring match against rendered message content.
- A classified outcome whose branches carry meaningfully different data — model_access.classify()'s Outcome today, more as they land — is a named, closed set of outcome types, never a raised exception. A result whose branches differ only by one field, like DagResult, doesn't need this shape.
- plugins.py and plugin_manifest.py never import conversation.py — anything needing both lives in a separate module that imports conversation.py, keeping PLUGINS' leaf modules acyclic.
- A plugin graph's node receives only its immediate predecessor's output, never the run's accumulated history.
- A `call` node's body runs only after `approve()` returns `True` — never unconditionally, and never for `ask`/`compute`/`route`/`stop`.
- A registry or dispatch seam for a family of pluggable backends earns its cost only once a second real member exists to register — build the single member as a direct call, not a lookup table of one, no matter how certain a second member seems.
- A CLI subcommand's handler returns an int for its own exit code; argparse's own `--version`/`--help`/parse-error exits are never wrapped or re-raised as something else.
- `conversation_store.bind_persist()` is rebuilt fresh before every turn, never reused across turns — its snapshot-at-bind-time budgets/`next_turn_seq`/`context_state` go stale otherwise, the same shape of risk `build_dispatch()` already documents in `docs/reference/dispatch_closure_state_bug.md`.

## Glossary

- sadana backbone: The foundational architecture of sadana-harness
  establishing its core paradigm constraints, state contracts, and abstraction interfaces.
  It acts as an adaptable substrate (or graft host) capable of absorbing the 21 L1 reference blocks
  from hermes-agent without architectural compromise,
  providing a flexible and robust base to build toward MVP and PMF.
- SDLC: Software development lifecycle
- AI-native SDLC: A reimagined SDLC that works around ai agents with a 6 stage loop,
  AI agents run through the loop and Humans stand above the loop instigating, directing and governing

## The hermes-agent paradigm and our own paradigm shift

In the current state of AI, most AI agents are amnesiacs. Agent skills are a step forward, an AI skill is pure knowledge
it is nothing more, it is an extension of a prompt and however hard you try to describe a procedure inside
an ai skill, it is flawed in its non-deterministic probabilistic nature.

The hermes-agent paradigm is to be a self-improving agent framework engineered with a closed learning loop.

- It maintains persistent memory to learn the user's project and preferences (with honcho)
- It maintains procedural memory, accumulating knowledge by creating itself its own skills
  in order to never forget how it previously solved a problem
- It has multiple features dedicated to make a doers life easier

We find multiple flaws with the “hermes way” of doing things:

- agent-authored skills get messy at scale and must adopt a single-tenant infrastructure;
- Because skills are only institutional knowledge, they aren’t self-sufficient;
- Only the AI agent is supposed to learn, the Humans are out of the question.

We want to empower our agent, with two things:

- Persistent memory: The same way that hermes-agent uses honcho, with our twist
  - we keep in memory what we decided, based on a known set of guidelines
- Plugins, in our own way:
  - skills belong in a plugin (the knowledge now becomes catered to the procedure)
  - a plugin has a deterministic DAG-like procedure
  - a plugin can interact with foreign environments (mcp, database, webhooks, apis)
  - a plugin handles the agent input and serves an output (link, md file)
  - That way an agent using a plugin becomes an agent following an SOP. Think of it like a zapier automation.

In life, you can distinguish knowledge and behavior.
Learning does not mean knowing, Learning actually describes that, given the same conditions you will produce a different behavior
For hermes-agent project, learning means “an ai writes a skill, meant to be only knowledge, as if it was a procedure and updating it in a way that changes behavior”
For sadana, learning means “a human creates a plugin, updates the plugin to change behavior”

## Core Philosophy & Strategy

sadana-harness is a reconstruction. `../hermes-agent` is a working agent
runtime — 2.3M lines over 27,000 commits you will take inspiration from.

The hermes-agent codebase has previously been measured into two levels: L0 and L1.
L0 is the coarsest partition of the hermes-agent system,
we divided their codebase in 6 chunks, and one of them is what we call L1.
L1 is the core of the hermes-agent system: including test files it reaches >50% of their repo,
we divided L1 into 21 reference blocks, and the order those blocks must be built in is already known.

We've planned to split the SDLC in three phases:

- Phase 1: From nothing to the "sadana backbone"
- Phase 2: From the "sadana backbone" to a MVP (Minimum Viable Product)
- Phase 3: From a MVP to PMF (Product Market Fit)

(current phase: Phase 1)

### During phase 1:

We will focus on scalability of what we create and copy the hermes-agent solutions
without compromising on own core paradigm.
This repository will seem empty, and that is the plan working, not a problem to solve.
We are not discovering what to build. We are rebuilding a shape someone else
arrived at by accident, on purpose, in the order they could not use.
Three consequences you might encounter:

- Early steps might have no callers yet, deliberately
- The order is evidence, not preference
- "What should we build first" is already answered

### During phase 2:

We will focus on the features that will actually make the paradigm-shift.

### During phase 3:

We will iterate on our MVP, sand off the rough edges, and focus on stability, performance and user experience toward PMF.

## Layout and ownership informations

`docs/reference/hermes_core_blocks_kind.csv` is generated and must never be
hand-edited; every other file under `docs/reference/` is hand-authored and
tracked like source. A pre-commit check fails any citation to a
`docs/reference/` path that isn't tracked or that exception.

A Make target is one line. The moment it needs branching, environment setup or
error handling it becomes a script in `scripts/`, and the target calls that.

Lint and format belong to pre-commit; ruff's arguments live in
`pyproject.toml`. Never add ruff or formatting flags to the Makefile, and
never add mypy to pre-commit — `make typecheck` owns static types.

## How work flows

You will apply the AI native SDLC, this framework was built around your capabilities.
1 work item = 1 artifact chain = 1 commit. 4 files, 6 stages:

    plan      intent.md    /plan-skill
    design    spec.md      /design-skill
    build     plan.md      /build-skill      (+ the code diff)
    test      —            no file: evidence, pasted
    deploy    review.md    /deploy-skill
    maintain  —            no file: a NEW intent.md, restarting at plan

Test and Maintain commit nothing of their own. Test produces the literal output of `make verify`,
which lands in `review.md` under `## Evidence` where the person deciding can see it.
Maintain produces a diagnosis written as a new `intent.md`, which re-enters this chain at the top.

Start a work item with `scripts/artifact.py new <ID> <slug>`.
`scripts/artifact.py status` says where you are and what is missing.
Artifacts live in `docs/tasks/<ID>-<slug>/`.

A hook enforces this. Writing `src/` or `tests/` without a valid `plan.md`
is refused, and so is writing a stage's artifact before the previous stage validates.
When you are blocked, the message names the missing thing — fix that, do not route around it.

Each artifact has a **trace section** that only gets filled if the methodology
actually ran: `Changed during planning`, `Concerns`, `Risks`, `Findings`.

Never write a plausible trace for work you did not do.

### Built-in skills cheat sheet

Use these built-in skills when relevant:

- **/loop [interval] [prompt]**: Use to schedule recurring tasks or periodic monitoring.
  - _Example:_ `/loop 20m check the deployment` or `/loop check status every 1h`
- **/debug [issue description]**: Use when encountering unexpected behavior, tool failures, or errors in the current session to enable and inspect session logs.
  - _Example:_ `/debug deployment command keeps failing with connection refused`
- **/batch [instruction]**: Use to execute large-scale codebase migrations or multi-file refactors by decomposing the work into parallel background agents.
  - _Example:_ `/batch migrate old-config to new-config across all services`

### Verifying your work

- Chain: `make chain` (must end "CHAIN OK")
- Lint: `make lint` (must end "LINT OK")
- Types: `make typecheck` (must end "TYPES OK")
- Test: `make test` (must end "TESTS OK"; all green)

`make verify` runs all four, cheapest first, and ends "VERIFY OK".

Run `make verify` before reporting any task complete, and paste the output.
A run that ends without its sentinel did not pass, however little red there is;
an empty suite exits 5 and is a failure here, not a green run.

That pasted output is the Test stage's whole deliverable. At Deploy it goes
into `review.md` under `## Evidence`; before then it goes in the conversation.
When CI exists it will move to the PR check run and `## Evidence` can go.

If a test fails, fix the code, not the test.
You can only fix a test created in the same task/work item/artifact only if the test itself is wrong,
then you are authorized to diagnose and repair it in the same move.
If you aren't authorized, escalate to the user.

## Tests

How to write a test is in `testing-conventions` skill, load it
before writing, changing or reviewing one.

### Important rules regarding tests

- **One door.** Never invoke pytest directly. Always `make test`, which runs
  `scripts/run_tests.sh` and pins timezone, locale, hash seed and a blanked
  credential environment. A bare `pytest` is not a faster test run — it is a
  different environment that happens to import the same files.

- **Fixing a bug: write the failing test first.** Commit it on its own, red.
  Then make it pass without touching it. A test that existed before the fix and
  could not be rewritten is the only real proof the bug is gone.

## After you ship

The maintain stage. In phase 1 nothing is in production yet, so "shipped" means merged to
main and the triggers are manual: something you notice, a test that starts
failing, a behaviour that does not match what `intent.md` promised.

Its output is a **new `intent.md`** — the anomaly and its evidence, a proposed
outcome, the affected systems, the open questions — entering this chain at the
plan stage like any other work item. That is why there is no `maintain.md`: a
maintenance finding worth recording is worth a work item, and one that is not
worth a work item is worth a sentence in conversation.

Never patch a closed work item. Its chain records what was decided and when;
editing it afterwards destroys the only thing it was for.

## The reference corpus

`../hermes-agent` is read-only. Writes to it are denied.

The index is `docs/reference/hermes_core_blocks_kind.csv` — 2,368 rows,
`block,tier,kind,filename,path`, one per file in hermes's core, across 21
purpose blocks.

    awk -F, '$1=="CONFIG" && $3=="production-code" {print $5}' \
      docs/reference/hermes_core_blocks_kind.csv

`build-skill` explains the columns and how much of a block to read. Finding
files in that index is your job — never ask the user which files are in a
block.

### Our methodology for learning from the reference

**We copy decisions** Read the reference to learn how a problem was solved and what it cost,
then try to write our own answer. Copying is encouraged when it saves time,
they have had this codebase for years, the only problem is that it is catered for different end goals.
However, you are responsible for auditing the copied code to ensure it matches our paradigm and does not introduce hidden dependencies or assumptions

**If you decide to copy their work** Really ask yourself how this will interfere with our paradigm,
watch out for tightly coupled, hidden assumptions in hermes-agent's codebase that will silently break when
transplanted into our codebase with our different paradigm.

## Research Subagents

- When delegating research to a subagent, state the deliverable explicitly: "Return a written report in your final message. Do NOT create or edit any files."
- Require the subagent to cite file paths and line numbers for every claim; a report without citations is incomplete and must be relaunched.
- For reference-implementation reviews (e.g. hermes files), make sure the agent is considering all the relevant files in the scope you gave him, he will find it in /docs/reference/hermes_core_blocks_kind.csv

## Things Claude gets wrong

Add an entry the second time a mistake repeats. Two so far:

- **`import sadana` works because of `pythonpath = ["src"]`** in
  `pyproject.toml`. Do not "fix" an import error with a `sys.path` edit or an
  editable install.
- **Do not activate `.venv`.** The Makefile puts it on PATH. A session that
  activates it and then runs a bare command is testing an environment CI will
  not reproduce.
