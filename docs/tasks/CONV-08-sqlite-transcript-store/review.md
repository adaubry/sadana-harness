# Review: A conversation survives the process that was running it (from plan.md 2026-09-04)

Reviewed: `git diff HEAD` (nothing in this work item is committed yet — the diff is the full uncommitted working tree against HEAD) — 6 files, +1314/-0
Reviewer context: same session as build. Per deploy-skill §1's "buy the separation instead of assuming it," the actual three-pass review was delegated to a fresh subagent given no prior context beyond `git diff HEAD` and `intent.md`/`spec.md`/`plan.md` — it re-ran `make verify` itself independently rather than trusting a result carried forward, and spot-checked two of spec.md's hermes-agent citations directly against the reference files.
Second opinion: none — ran during build (self-check: `/ponytail-review` + `/simplify`, four parallel angles), not repeated here by design (see `docs/tasks/SDLC-second-opinion-timing/`).

## Evidence

```
$ make verify
docs/tasks/CONV-08-sqlite-transcript-store: all present artifacts valid
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
LINT OK
Success: no issues found in 5 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
186 passed in 2.02s
TESTS OK
VERIFY OK
```

Note on this evidence: the first `make verify` runs during build stage were misleadingly green — `git status` showed the two new source files (`src/sadana/conversation_store.py`, `tests/unit/test_conversation_store.py`) as untracked, and `make lint` (`pre-commit run --all-files`) only lints files `git` already knows about, so pre-commit's ruff/ruff-format hooks silently never ran against either new file until this deploy stage ran `git add -N` on them to bring them into scope. That run then surfaced three real lint findings (an `SIM105` guarded-rollback simplification, a `B008` mutable-default-style warning on a frozen-dataclass test default, an `SIM117` nested-`with` merge) — all fixed, all mechanical, all things `make verify` itself now enforces and none reported below per deploy-skill §6. Raised here because it's a real gap in this project's own verification loop, not specific to this work item: **a new, never-`git add`ed file can pass `make verify` without ever being linted.** Worth a maintain-stage `intent.md` of its own — flagging rather than fixing in this diff, since a `Makefile`/pre-commit change is outside this work item's scope.

## Findings

Three-pass review (bugs, security, compliance) run by the delegated cold-review agent against `intent.md`, `spec.md`, `plan.md`, and the diff.

**Compliance verification performed:** every `plan.md § Proof` item traced to what discharges it (the self-check output not being independently re-derivable from the diff alone was noted, not treated as a gap — it ran in this same session's build stage, above); every `spec.md § Acceptance criteria` bullet (9 total) matched to a specific test by name; `spec.md § Rejected alternatives` checked for drift on all four items (none found); the five design principles checked explicitly, including spot-verifying two of spec.md's hermes-agent citations (`hermes_cli/sqlite_util.py`'s `write_txn()`, `hermes_state_common.py`'s `state_meta`/`sessions` PK shape) directly against the reference files rather than trusting the citation; every file in the diff cross-checked against `plan.md § Files that change` (exact match, no undeclared file touched or promised-but-untouched file missing).

### Important

- [Compliance] `spec.md § Interface` documented `save(conn, conversation, *, now: float) -> None`, but the shipped `save()` in `src/sadana/conversation_store.py` (and `_insert_messages()`) adds an undocumented `start_seq: int = 0` parameter, which `bind_persist()`'s `nonlocal flushed` closure depends on to avoid re-sending already-durable messages every tool round. It was functionally harmless — the default kept every call site spec.md described correct, and every acceptance criterion still passed — but it was a real interface addition and a real piece of mutable state (design guideline 5) that spec.md's Interface/Design sections did not mention or justify, added during this session's build-stage self-check (the efficiency/altitude finding that `bind_persist()` was O(messages²) per turn), after `spec.md`/`plan.md` were already written.
  **Fixed post-approval**: `spec.md § Interface`'s `save()` entry now documents `start_seq` and its rationale; a new `§ Concerns` bullet justifies the mutable state per guideline 5 (accepted, not reversed — the alternative is real O(messages²) I/O for zero behavioural difference) and states its one real limitation (the `flushed` counter's O(messages) guarantee holds only within one closure's own repeated calls, not across turns, since nothing wires `bind_persist()` into `take_turn()`/`run_child()` yet). No code changed; `make verify` re-run clean after the doc edit.

### Nits

- [Compliance] `bind_persist()`'s `flushed`-tracking optimization only holds for the lifetime of one closure. Nothing wires `bind_persist()` into `take_turn()`/`run_child()` yet (by design — requirement 9), so the natural future call pattern (one `bind_persist()` per turn, or resuming a loaded conversation with N already-durable messages) would reset `flushed` to 0 each time; the module's own docstring claim ("a turn's total persistence work is then O(messages), not O(messages²)") holds within one closure's calls but doesn't state that scope.
- [Compliance] `_SCHEMA`'s `messages` table declares `conversation_key TEXT NOT NULL REFERENCES conversations(key)`, but `open_store()` never issues `PRAGMA foreign_keys=ON` — SQLite disables FK enforcement by default, so this is decorative, unlike the two real `PRIMARY KEY` constraints the same schema declares. Harmless today (this module's own API always writes the parent row in the same transaction as any child rows), but the DDL implies a guarantee the database doesn't actually keep.
- [Bugs] None. The round trip (`create`/`save`/`load`), two-table transaction atomicity (both forced-failure tests), and `bind_persist()`'s persist-before-dispatch path were traced against their documented contracts and the acceptance criteria; every claim held under `make verify`.
- [Security] None. Every SQL value (`conversation.key`, message fields, budget numbers) is a bound `?` parameter in `create()`/`save()`/`load()`/`_insert_messages()`; the only string-built SQL is column/table names assembled from the fixed `_CONVERSATION_COLUMNS` tuple and the literal `_SCHEMA` text — never from caller-supplied values.

### Raised, not findings

- The `make verify`/untracked-file lint gap described under Evidence above — a real process gap, not a defect in this work item's own code, and outside this diff's scope to fix.

## Decision

Approved by Adam, 2026-09-04, with the Important compliance finding
fixed (spec.md's Interface/Concerns brought in sync with the shipped
`start_seq` parameter and the mutable state behind it) before merge, as
recorded above. Nits accepted as-is, not fixed.
