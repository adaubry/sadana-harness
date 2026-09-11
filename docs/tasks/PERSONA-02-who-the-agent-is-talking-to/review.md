# Review: Who the agent is talking to (from plan.md 2026-09-11)

Reviewed: 14e55aa..working tree — 26 files, +1435/-90
Reviewer context: **same session as the build, and that is a real limitation.**
PERSONA-01 got two fresh subagents with no context beyond the diff; this item's
four self-check agents and both cold reviewers died on a session rate limit
(resets 14:00 Europe/Paris), so every pass below was run by the session that
wrote the code. A reviewer who remembers writing something reviews its
intentions. Read the Bugs and Compliance findings with that discount applied,
and re-run the delegated review before this is treated as having had one.
Second opinion: `/ponytail-review` and `/simplify` were attempted during Build
and failed the same way; their passes were run in-session too.

## Evidence

```
$ make verify
docs/tasks/PERSONA-02-who-the-agent-is-talking-to: all present artifacts valid
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
Success: no issues found in 44 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
............................................................             [100%]
780 passed in 27.42s
TESTS OK
VERIFY OK
```

This diff changed two signatures — `conversation_store.create()` gained a
required keyword, `gateway_dispatch.handle_inbound()` gained an optional one —
and `scripts/` is outside both `testpaths` and `mypy src`, which is exactly how
PERSONA-01 shipped four broken call sites. An AST sweep of every call site in
the repo, `scripts/` included:

```
$ python -c '<ast walk over every *.py, matching create( and handle_inbound(>'
ok     create  src/sadana/client_surface.py:277
ok     create  tests/unit/test_conversation_store.py:125,135,139,148,168,190,250,260,277,
                287,355,390,391,403,404,414,420,430,436,451,470,478,479,613,621,622,623,634
ok     create  tests/unit/test_client_surface.py:185, test_gateway_dispatch.py:147,
                test_persona_store.py:154,175,185, test_subcommands_chat.py:140,237,
                test_subcommands_conversations.py:22, test_subcommands_persona.py:170,240
ok     handle_inbound  src/sadana/scheduling.py:79            account=True
ok     handle_inbound  src/sadana/subcommands/gateway.py:78   account=False
ok     handle_inbound  scripts/prove_gateway_wait_e2e.py:169, prove_gateway_webhook_e2e.py:164,
                       prove_setup_fresh_instance.py:288      account=False
ok     handle_inbound  tests/unit/test_gateway_dispatch.py:38,42,65,90,103,121,151,193,213,232,245

56 call sites, 0 stale. The only caller in src/ that states an account is
scheduling.py:79 — which is requirement 3 read back out of the code.
```

## Findings

Three passes — Bugs, Security, Compliance — plus the file-list reconciliation,
all run in-session against the diff and the three artifacts. Two Important
findings were fixed here with tests; three are open, one of which is the review
limitation itself. Five nits.

### Important

- [Compliance, FIXED] Acceptance criterion 2 — "a character selected by the
  owner is the voice of a newly created scheduled conversation" — was
  undischarged. The diff proved the two halves separately (a fired trigger runs
  as the owner; a conversation is created in its account's voice) and never
  their composition, which is the whole claim of this work item. Fixed:
  `test_scheduling.py::test_a_scheduled_conversation_speaks_in_the_owners_chosen_voice`
  drives a real `tick()` through the real bridge and asserts the created
  conversation's stable prompt is the owner's chosen character, rendered.

- [Compliance, FIXED] The file list had drifted twice over. `src/sadana/stores.py`
  is a module `spec.md` § Design never named, and `subcommands/memory.py`,
  `tests/conftest.py`, `tests/unit/test_subcommands_conversations.py` and four
  `create()` call sites were touched without `plan.md` naming them. Both are
  recorded in `plan.md` § Amended while implementing, with the reason each was
  not anticipated; the file list now matches `git status` in both directions.
  The underlying cause is worth stating plainly: the schema-ensuring gap
  `stores.py` closes was raised by PERSONA-01's altitude review and deferred by
  this project with "revisit when a third table exists". The third table is in
  this diff, and the deferral cost a runtime failure in a CLI verb.

- [Compliance, OPEN] The accounts listing shows the owner even when nothing in
  the store has used that account, which `spec.md` requirement 7 does not say —
  it defines the list as the union of three sources. The implementation
  (`subcommands/persona.py::cmd_persona_accounts`) adds the owner unconditionally
  so the listing cannot contradict `persona use`, which always accepts the
  owner. It is a deliberate, one-line divergence in the reader's favour, and it
  is still the spec being edited by the code rather than the other way round.

- [Bugs/Compliance, OPEN — the limitation itself] This review was not run cold.
  Both cold reviewers failed on the session rate limit, so the Bugs pass below
  is the author re-reading their own work a few minutes after writing it. The
  two nits it did find (case-insensitive `LIKE`, the suffix collision) are the
  kind a cold reader finds in the first ten minutes; the kind a cold reader
  finds in the last ten are not represented here.

### Nits

- [Bugs] `adopt_scheduled_memories` matches with SQLite `LIKE`, which is
  case-insensitive for ASCII, so an account hand-written as `SCHEDULE:x`
  through `sadana memory` would also be adopted. Nothing this project creates
  produces that key — `scheduling.py` always writes `schedule:` — but the match
  is looser than the prefix it is documenting. `GLOB 'schedule:*'` or a
  `substr()` comparison is exact.
- [Bugs] The collision suffix can itself collide: if the owner already holds
  both `k` and `k--schedule:daily`, a second adoption of a hand-written
  `schedule:daily` entry keyed `k` overwrites the earlier suffixed row. It
  needs somebody to write a `schedule:` memory by hand after an adoption has
  already run, and "loses nothing" is then one row short of true.
- [Efficiency] `cmd_persona_accounts` runs one `get_selection` per account, and
  `account_exists` builds the whole sorted union to answer a membership
  question. Both are CLI-only, over a handful of rows on a personal
  installation; neither touches a turn.
- [Compliance] `persona_selections.updated_at` is still written and never read
  — carried from PERSONA-01, unchanged here, and now joined by
  `conversation_accounts`, which nothing reads during a turn either (by design;
  it exists for the listing).
- [Bugs] Roughly thirty test lines now carry `# pragma: allowlist secret`
  because `detect-secrets` reads `account_key="..."` as a keyword-shaped
  secret. It is the repo's own documented false positive and the same pragma
  already sat on `account_key` lines in `test_memory.py`, but it is thirty
  lines of noise bought by one parameter name.

### What was checked and found clean

- **Security**: the account override is keyword-only and reachable only from
  Python. `MessageEvent` gained no field; `channel_webhook.parse_webhook_request`
  still builds an envelope from four known keys and ignores the rest, pinned by
  `test_an_account_in_the_payload_is_not_an_account` — a payload claiming
  `"account": "adam"` still runs as `webhook:chat-9`. Every account value is a
  bound SQL parameter, including the composed collision key; no f-string
  reaches SQL. The adoption's `LIKE` pattern is parameterised.
- **Rejected alternatives**: all six re-read against the diff. No drift. In
  particular there is no `account_key` column on `conversations`, no field on
  `MessageEvent`, no migration command, the listing reads all three sources
  rather than memory alone, and rubric overrides and character selections are
  not moved (with a test for the negative).
- **Proof**: every item in `plan.md` § Proof traces to a named test —
  `test_memory.py` (3 owner cases), `test_memory_store.py` (6 adoption cases
  including collision, idempotency and the settings negative),
  `test_conversation_store.py` (the account row, the per-account distinct set,
  the legacy row), `test_scheduling.py` (owner account, unchanged conversation
  key, and now the voice), `test_gateway_dispatch.py` (webhook derivation, the
  payload claim, the trusted override), `test_persona_store.py` (three sources,
  one at a time, plus the union), `test_subcommands_persona.py` (the listing,
  the refusal, the owner exception, an account with only a conversation).
- **Design principles**: prior art cited and priced — hermes's
  `gateway/platforms/base.py:3347-3357` is what argued the envelope field out
  of the design, and their allowlist framework was declined with a reason.
  Nothing added a registry (`stores.py` is two calls in a fixed order, and says
  so). The turn path gained nothing; the two heavier steps are conversation
  creation and process start. The only new stored state is one row per
  conversation naming its account; the owner is derived from config on every
  call and written down nowhere.
- **Acceptance criteria**: eleven of twelve satisfied and traced. The twelfth —
  "a fact remembered during a scheduled run is visible in `sadana memory list
  <owner>`" — is discharged by composition rather than by its own test: the
  account reaching `take_turn` is the owner's (proved above end to end) and
  memory writes are keyed by that account (MEMORY-01's own tests). Saying so
  here rather than claiming a test that does not exist.

### Raised, not findings

- The adoption is a one-time data migration living on a path that runs
  forever. `spec.md` § Open questions already asks when it may be deleted;
  nothing decides, and it will still be running on every process start a year
  from now.
- `scripts/` is still unverified — not in `testpaths`, not in `mypy src`,
  imported by no test. This diff changed two signatures and needed an AST sweep
  to know it was safe. That is the second work item in a row where the sweep was
  the instrument that answered the question, which is an argument for putting
  `scripts/` under `mypy` in its own work item.

## Decision

Approved by Adam, 2026-09-11, without waiting for the delegated cold review.

The recommendation put to them was the opposite — hold until the rate limit
reset and re-run the cold pass, because this change touches identity, a data
migration with no undo, and two signatures. They approved anyway, and this
records that the trade was made knowingly rather than missed: the review that
stands behind this merge was run by the session that wrote the code, and the
three open findings above ship with it. If the cold pass is ever run against
this commit and finds something, it re-enters as a new `intent.md` at the
maintain stage like any other finding — nothing here is closed by having been
approved.
