# Spec: A conversation survives running out of room, and a bulky tool result stops crowding it out

Intent: docs/tasks/C11-context-completion/intent.md

## Requirements

1. When a model rejects a request as too large (`ContextOverflow`), the
   conversation's history is genuinely shortened — a real second model call
   summarizes the portion being dropped — and the turn continues, instead
   of always exiting `CONTEXT_OVERFLOW_UNHANDLED`.
2. A tool result too large to keep inline is saved in full to a local file
   and replaced, in the conversation's own record, with a short reference
   (path, size, a preview) — never silently truncated with the rest
   permanently lost, as happens today.
3. A compaction event never splits an assistant message carrying
   `tool_calls` from any of the `tool` rows answering it — the cut always
   lands on a safe boundary.
4. A second compaction event recognizes a summary an earlier compaction
   already produced and asks the model to refine it, rather than
   resummarizing it as if it were raw conversation — avoiding
   summary-of-a-summary degradation.
5. `Conversation.stable_prompt_len` (C10) is revisited: either compaction
   is shown to never put it at risk, or it is kept correct. (Resolved
   below: never at risk, because compaction never touches `system_prompt`.)
6. The `mark_cache_boundary`/`_eligible_trailing_indexes` index-0
   assumption, accepted-not-fixed at C10's own close, is fixed — the
   system message is located by role, not by position.
7. Nothing here designs EXECUTION's, SESSION-STORE's, or LIFECYCLE's own
   interface. Retrieving a spilled result is out of scope — no tool exists
   yet for a model to re-read one.
8. Proactive (percentage-of-context-window) compaction is out of scope —
   compaction stays reactive, triggered only by the `ContextOverflow` this
   project already raises.

## What the reference corpus showed

Two research passes, each scoped to avoid reading multi-thousand-line
files linearly (grep for structure first, read only the sections that
answer a specific question).

**Compaction** — `agent/native_compaction.py` (569 lines, read in full),
`agent/context_compressor.py` (8,842 lines, targeted: threshold
computation `:3288-3357`, the summarization call `:4918-5401`,
reassembly `:7641-8300`, tool-pair sanitization `:6153-6270`),
`agent/conversation_compression.py` (docstring + grep only),
`agent/error_classifier.py` (grep for the context-overflow classification
sites). Findings, at the decision level:

- Two independent triggers exist: proactive, at a computed percentage of
  the model's context window (`context_compressor.py:3789-3819`), and
  reactive, when a provider's own rejection is classified as a context
  overflow (`error_classifier.py:357-387`). This project has only ever
  built the reactive one (`model_access.NeedsContextCompression` →
  `ContextOverflow`) — staying reactive-only (intent's own constraint;
  confirmed again during this design's interview) is not a corner cut,
  it is declining a whole second trigger mechanism this project has no
  other use for yet.
- The summarization call itself goes through hermes's own generic
  auxiliary-model-call path (`call_llm(task="compression", ...)`,
  `context_compressor.py:5250-5300`) — the same layer every other
  incidental model call uses, exactly matching B2's own framing
  ("CONTEXT's compaction path... is just another caller of `send()`").
  This project has one caller-facing entry point for a model call already
  (`conversation.complete()`); the design below reuses its underlying
  retry mechanics rather than inventing a second path.
- Reassembly is head (protected) + one summary message + tail
  (protected), never splitting a tool-call group at the cut
  (`_align_boundary_forward/backward`, `context_compressor.py:6264-6349`;
  `_sanitize_tool_pairs`, `:6153-6270`) and never byte-slicing a summary
  that doesn't fit its budget — dropped whole rather than truncated,
  because truncating structured text corrupts it (`:344-351`).
- Repeat compactions send the *previous summary* plus only the *new*
  turns and ask for an updated, iterative summary rather than
  resummarizing from scratch (`context_compressor.py:5210-5232`) — this
  is adopted, not declined (see Requirement 4); the alternative
  (resummarize the same ground repeatedly) visibly degrades over a long
  conversation.
- Terminal failure (auth, quota, network, empty content) aborts and
  leaves the conversation unchanged rather than degrading it
  (`context_compressor.py:8090-8122`).

**Declined, with the specific cost named**: proactive/percentage
triggering (a second trigger mechanism with no second consumer here);
the chat-template role-alternation dance in `_merge_summary_into_tail`
(exists to satisfy strict multi-provider chat templates; this project
has one provider with no such constraint); durable, DB-persisted
cross-restart cooldown/streak state (justified by gateway sessions
resuming from a shared store; a single in-process loop needs none of
that); the entire OpenAI-Responses server-side compaction reconciliation
path in `native_compaction.py` (exists only because hermes has a second,
competing compaction mechanism to reconcile against — this project has
one).

**Result-spilling** — `tools/tool_result_storage.py` (450 lines, read in
full). The other two files named in the original research brief turned
out not to be the mechanism: `tools/tool_output_limits.py` is a
different, narrower feature (per-tool truncation caps, no persistence)
and `agent/context_references.py` is `@file:`/`@diff:` prompt-expansion,
unrelated to spilling a tool *result*. Findings:

- Threshold-based, plain size in characters (`maybe_persist_tool_result`,
  `tool_result_storage.py:344`; default 100,000 chars,
  `tools/budget_config.py:17`).
- Spills to a plain file under a state-owned cache directory (never the
  OS temp dir, so it shares the same housekeeping as other state — this
  project has no equivalent housekeeping process, so only the write
  itself is adopted, not the pruning story — see Non-goals), plain UTF-8
  text (`tool_result_storage.py:75-79,154`).
- The in-context stand-in names the path, the original size, and a
  preview (`_build_persisted_message`, `generate_preview`,
  `tool_result_storage.py:231-296`).
- Retrieval is real in hermes: the stand-in tells the model to call its
  own `read_file(path, offset, limit)` tool. This project has no such
  tool — EXECUTION, the block that would own a general-purpose read
  capability, doesn't exist yet. The stand-in here is an honest dead-end
  pointer for now (Requirement 7) — a person can go find the file; the
  model cannot yet ask to see it again. Named explicitly rather than
  glossed over, matching CLAUDE.md's own instruction not to add error
  handling for a scenario that can't happen — here the inverse: not
  pretending a capability exists that doesn't.
- A write failure never raises; it falls through to the caller's own
  existing truncation as a safety net (`tool_result_storage.py:150-159`).

**Declined, with the specific cost named**: remote-sandbox path
translation (no sandbox abstraction exists here); the MCP-specific
threshold tier and the turn-level aggregate spill budget (no MCP
integration, and a per-result threshold already satisfies the intent's
own "a tool call returns something too bulky," not "many medium results
combine to overflow"); the context-window-scaled budget (one fixed,
config-driven threshold is this project's own established pattern
elsewhere); the identical-call dedup stub layer (a separable feature);
gateway-cron pruning (needs a scheduler this project doesn't have — a
spilled file here is left to accumulate, named as an open question, not
silently handled).

## Design

Five pieces of new or changed surface: `context.py` gets both remaining
real checkpoints; `model_access.py` gains one shared retry primitive so
the new checkpoint doesn't duplicate `complete()`'s own loop;
`conversation.py` gains a `Message` field, two new sanctioned
history-shape functions, a reordered TOOL_ROUND, and a refactored
`complete()`; a brand-new `result_spill.py` owns the one piece of real
disk I/O this item introduces. The order below follows the one
architectural correction everything else depends on first.

### The system-prompt/history split — a correction to a prior assumption

`docs/reference/conversation_block_blueprint.md` §4.3 (the document C7's
own `rotate_prompt()`/`PromptRotationReason` trace to) assumed
compression rebuilds the *system prompt*: "The prompt is built once per
session... rebuilt only by compression." Reading hermes's actual
`compress()` shows this is not what real compaction does — it reassembles
the *message list*; the system prompt is untouched. This is the same
"blueprint is a guide, audited every time" correction C2, C6, and C9 each
already made once.

Consequence, agreed with the user rather than assumed: `turn_complete`'s
return becomes a structured value with **independent, both-optional**
fields — one for a system-prompt rewrite, one for a compaction outcome —
not a forced choice between them, and not a collapse to "history only."
This means `rotate_prompt()`/`PromptRotationReason.COMPRESSION` (C7/C8)
are **not deleted**: they stay a real, available mechanism for the
system-prompt-rewrite case; this work item's own real compaction simply
never populates that field, because real compaction only ever touches
history. `take_turn()`'s existing rotation-detection logic (comparing the
returned `system_prompt` against `conversation.system_prompt`) needs no
change at all — it already does the right thing for a field that happens
to stay `None` every time.

One consequence resolves Requirement 5 for free: since compaction never
touches `system_prompt`, `stable_prompt_len` (the cache-boundary length
C10 built, defined relative to `system_prompt`) is never at risk. The
open question C10 left closes as "no action needed," not as a new
mechanism.

### `src/sadana/context.py`

- `TurnCompleteResult` (frozen dataclass): `context_state: ContextState`,
  `new_system_prompt: str | None = None`, `new_summary_text: str | None
  = None`. Both `None` carries the same meaning today's single `None`
  already had: "not compressed."
- `compaction_tail_messages_from_config() -> int` — reads
  `SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES` (default 10) via
  `config.env_int`. The one policy number this checkpoint owns — how much
  of the recent conversation compaction must never touch — matching
  `trailing_marks_from_config()`'s own precedent (C10).
- `result_spill_threshold_from_config() -> int` — reads
  `SADANA_CONTEXT_RESULT_SPILL_CHARS` (default 100,000, matching
  `SADANA_CONVERSATION_TOOL_RESULT_CHARS`'s own existing default, so
  spilling handles everything that today gets silently truncated).
- `turn_complete(state, history, system_prompt, tail_start, provider,
  model) -> TurnCompleteResult` becomes `async`. `tail_start` is a
  boundary index the caller already computed (see
  `find_compaction_boundary` below) — `turn_complete` never decides where
  it is safe to cut; it only decides *what to say* about
  `history[:tail_start]`. If that slice's first message has
  `is_summary=True`, the prompt asks the model to refine it using the new
  turns since, instead of resummarizing it as raw conversation
  (Requirement 4). Builds one `model_access.Request` (`tools=()`, no tool
  calling for a summarization request), resolves it via the new shared
  primitive below, and returns the model's text as `new_summary_text` —
  or leaves it `None` on any failure (a `Response` with empty content,
  or any other outcome), matching hermes's "terminal failure aborts,
  leaves the conversation unchanged" posture, without porting its
  static-fallback-summary path (declined — see Rejected alternatives).
  Never constructs a `Message` — see the import-cycle note below.
- `after_tool_result(state, result: Message) -> Message` becomes
  `async`. If `len(result.content or "")` is at or under
  `result_spill_threshold_from_config()`, identity (unchanged from C10).
  Otherwise calls `result_spill.write_and_reference` (wrapped in
  `asyncio.to_thread`, matching `conversation_store.py`'s own precedent
  for wrapping even local disk I/O, `bind_persist:270`) and returns
  `dataclasses.replace(result, content=<reference note>)` — modifying an
  *existing* `Message` instance needs no import of the class itself, so
  this introduces no cycle.

**Why `turn_complete` never constructs a `Message`.** C10 established
that `context.py` has zero runtime dependency on `conversation.py` or
`model_access.py`; `conversation.py` imports `context.py`, not the other
way around. Constructing a *new* `Message` (the summary) would require
importing the class at runtime — a real cycle. Modifying an *existing*
one (`after_tool_result`'s case) does not, since `dataclasses.replace`
needs no class reference. So `turn_complete` returns a plain string, and
`conversation.py`'s own `run_turn` builds the actual `Message` — keeping
`context.py`'s "deals only in plain data" posture from C10 intact, with
no exception carved out for this item.

### `src/sadana/model_access.py`

- `resolve(request: Request) -> Response | NeedsCredentialOrProviderChange
  | NeedsContextCompression | Degenerate | Abort` — extracts the "loop
  while `send()` returns `Retry`, return the first non-`Retry` outcome"
  mechanics that `complete()` already has, so a second caller
  (`context.turn_complete`) doesn't reimplement it. `model_access.py`
  stays fully unaware of `ContextOverflow`/`ProviderFailure` — each
  caller still does its own translation from the returned outcome, so
  this adds no new dependency, only removes duplicated looping.
- `complete()` (in `conversation.py`) is refactored to call `resolve()`
  instead of looping itself — a mechanical change; its own existing
  tests are the proof this preserves behavior exactly (see Acceptance
  criteria).

### `src/sadana/result_spill.py` (new — touches real disk I/O, its own
file per CLAUDE.md's existing rule)

- `write_and_reference(tool_call_id: str, content: str) -> str` — writes
  `content` to a file under `config.get_paths().state_dir / "tool_results"
  / <sanitized tool_call_id>.txt`, and returns a short reference note
  (path, size, a preview of the first ~1,500 characters, and an explicit
  statement that no tool exists yet to re-read it — Requirement 7,
  stated in-context, not just in this document). A write failure (`OSError`)
  never raises: returns `content` unchanged, so the caller's own existing
  `_cap_tool_result` truncation becomes the safety net it already is
  today — identical to hermes's own "degrade to today's behavior, never
  raise out of the tool-result path."

### `src/sadana/conversation.py`

- `Message` gains `is_summary: bool = False` — an explicit, typed fact
  (every existing call site is unaffected by the default), not a
  content-sniffed heuristic. Matches this project's own established
  preference (C10 stored `stable_prompt_len` rather than re-deriving a
  boundary from concatenated text, for the same reason: don't re-parse
  what can be recorded once, honestly).
- `find_compaction_boundary(messages, keep_tail_count) -> int` — the
  largest safe cut index such that `messages[index:]` holds at least
  `keep_tail_count` messages when available, and never starts on an
  orphaned `tool` row (walks backward past any leading `tool`-role
  messages, mirroring `pending_tool_call_ids`'s own backward scan,
  Requirement 3). A pure function over `messages`; no I/O, no state.
- `compact(messages, tail_start, summary_text) -> tuple[Message, ...]` —
  the one new sanctioned way to *replace*, not grow, a conversation's
  history: `(Message(role="user", content=summary_text,
  is_summary=True),) + messages[tail_start:]`. `tail_start` must already
  be a safe boundary from `find_compaction_boundary` — this function
  trusts it, the same way `append()` trusts a caller-supplied `Message`
  rather than re-validating shape it didn't produce.
- `run_turn`'s `except ContextOverflow` branch: computes `tail_start =
  find_compaction_boundary(messages,
  context.compaction_tail_messages_from_config())`, calls
  `context.turn_complete(context_state, messages, system_prompt,
  tail_start, provider, model)`, and on a real result applies
  `messages = compact(messages, tail_start, result.new_summary_text)`
  and/or `system_prompt = result.new_system_prompt` — independently,
  matching the structured-result design above. `CONTEXT_OVERFLOW_UNHANDLED`
  fires only when *both* fields come back `None`.
- TOOL_ROUND reordered: `context.after_tool_result` now runs on the *raw*
  dispatch result, before `_cap_tool_result`'s character-budget
  truncation — today it runs after, which means truncation has already
  destroyed the data spilling exists to preserve (a real bug found while
  grounding this design in the current code, not present in any shipped
  behavior yet, so nothing regresses by fixing it here rather than as a
  separate item). `_cap_tool_result` still runs afterward, now a pure
  safety net against whatever `after_tool_result` decided to leave in
  context (the reference note, or the original content when under the
  spill threshold).

### Applied policies

- **testing-conventions**: `result_spill.py`'s tests write only to a
  `tmp_path`-redirected state dir (the existing autouse fixture), never
  the real one; the real model call inside `turn_complete` is tested with
  a mocked `model_access.send`, never the network — matching every other
  unit test in this codebase. The one live proof (a real compaction event
  against a real model, and a real oversized result actually spilling to
  a real file) is a standalone script outside `make test`, its output
  pasted as Deploy-stage Evidence — the same convention C10/CONV-09 used,
  not a relaxation of the unit suite's network ban.
- **project-structure** / **reference-lookup**: as C10's spec.md already
  noted, neither exists as an installed skill in `.claude/skills/`;
  conformance follows the same concrete precedents cited there (one flat
  module per block; a module touching real I/O is its own file).

## Interface

**CONVERSATION → CONTEXT**, both remaining checkpoints now real:

- `turn_complete(state, history, system_prompt, tail_start, provider,
  model) -> TurnCompleteResult(context_state, new_system_prompt,
  new_summary_text)` — both output fields independently optional.
- `after_tool_result(state, result) -> Message` — now `async`; identity
  under the spill threshold, a reference note above it.

**CONVERSATION-internal, new**: `find_compaction_boundary(messages,
keep_tail_count) -> int` (pure); `compact(messages, tail_start,
summary_text) -> tuple[Message, ...]` (the one new sanctioned
history-replacing operation).

**MODEL-ACCESS, new**: `resolve(request) -> Response | <a
non-Retry failure outcome>` — an addition to its named operation set
(`send()`, `context_window()`, `mark_cache_boundary()`), not a change to
any of them.

**CONTEXT → new I/O module**: `result_spill.write_and_reference(tool_call_id,
content) -> str` — the only place in this diff that touches disk.

## Acceptance criteria

- [ ] A live round trip shows a `ContextOverflow` recovered: the turn
      continues past it, and the resulting history contains exactly one
      message with `is_summary=True` in place of the summarized portion.
      **Corrected at deploy-stage review, after the fact this criterion's
      original wording ("a real `ContextOverflow`") turned out not to be
      affordably provable**: six real, paid API calls across two
      OpenRouter models (with `transforms` explicitly disabled) all showed
      a request gets silently truncated before it reaches real billing or
      inference, regardless of model or provider route — a genuine
      provider-side "too large" rejection isn't reproducible at any
      affordable scale today. Satisfied instead as a disclosed hybrid,
      approved by the user after being shown the real cost evidence and
      the alternative (keep spending indefinitely on more real attempts):
      the *trigger* (`model_access.send` returning `NeedsContextCompression`)
      is simulated for exactly one call; the real second model call, the
      real retry, and the resulting message shape are all genuine and
      unmocked. See `docs/tasks/C11-context-completion/review.md`'s own
      Evidence section for the full record and the cold review's own
      skeptical checking of this exact question before it was accepted.
- [ ] A second, later compaction in the same live proof shows the prior
      summary being refined (its content changes, and the prompt sent for
      the second call is shown to include the first summary rather than
      re-deriving one from raw history it no longer has). Same hybrid
      caveat as above applies to how the second overflow is reached, not
      to the refine behavior once reached — that part is checked directly
      against the real prompt sent, not just its resulting text.
- [ ] `find_compaction_boundary` never returns an index that would strand
      a `tool`-role message without its answering assistant message —
      tested directly with a fixture history built to try to trip it.
- [ ] A live round trip shows a real oversized tool result spilled to an
      actual file under the configured state directory, and the
      in-context reference naming that path is what the model actually
      receives.
- [ ] `complete()`'s existing test suite passes unchanged after the
      `resolve()` refactor — proof the retry-loop extraction preserved
      behavior exactly, not just similarly.
- [ ] The `mark_cache_boundary` index-0 `# ponytail:` comment
      (`src/sadana/model_access.py`) is gone, replaced by locating the
      system message by role.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

Proactive/percentage-of-window compaction. Any multi-provider
chat-template alternation handling. Durable, cross-restart compaction
cooldown/streak state. A static, non-LLM fallback summary on
summarization failure (a terminal failure here simply leaves the
conversation unhandled, matching this item's own smaller scope). Real
retrieval of a spilled result (no read-style tool exists — EXECUTION's
job, later). Remote-sandbox path translation for spilled files. Any
scheduled or cron-driven pruning of old spilled files — an open question
below, not silently solved. A turn-level aggregate spill budget across
multiple tool results in one turn. EXECUTION's, SESSION-STORE's, or
LIFECYCLE's own design.

## Open questions

Spilled files under `state_dir/tool_results/` are never pruned by
anything this work item builds — they will accumulate for as long as a
conversation (or many conversations) keep spilling results. Whether that
needs a real answer now, or waits for a genuine second occurrence
(matching this project's own "two occurrences" bar for promoting
something into a real backlog item), is left to the user; hermes's own
answer (a scheduled gateway prune) needs a scheduler this project doesn't
have yet.

**Found during build planning, not resolved here**: real compaction and
real persistence don't coexist safely. `conversation_store.py`'s
`_insert_messages` is `INSERT OR IGNORE` keyed on `(conversation_key,
msg_seq)`, on the explicit invariant that a message row is never edited
once written — history only grows. Compaction renumbers the tail after
one new summary message, so persisting a compacted conversation would
try to insert rows under seq numbers a stale, pre-compaction row already
occupies, and `INSERT OR IGNORE` would silently keep the old row. Checked
that this is currently inert: nothing outside
`tests/unit/test_conversation_store.py` calls `conversation_store.save()`/
`create()`/`bind_persist()` at all today. `Message.is_summary` also gets
no DB column, so a persisted-and-reloaded summary message would silently
lose its marking — same reasoning, same inertness. Revisit the moment
anything real calls persistence on a conversation that also compacts,
matching this project's own "two occurrences" bar; solving it properly
means deciding how a compaction event should be represented durably (a
new segment, a rewritten row set, something else), a design question of
its own, not a corollary of this one.

## Rejected alternatives

Collapsing `turn_complete`'s return to "a new message history" alone
(this designer's own first framing): rejected by the user directly — the
system-prompt-rewrite and history-compaction cases are decoupled
mechanisms with different lifecycles in every production harness checked
(this project's own prior blueprint reading, plus the user's own
knowledge of how Claude Code and hermes split them), and forcing one
return slot to serve both would make a future, real system-prompt-rewrite
need reopen this same signature a second time.

Deleting `rotate_prompt()`/`PromptRotationReason.COMPRESSION` now that
nothing calls them for real: rejected — the structured, both-optional
return keeps the mechanism genuinely available, not orphaned; deleting
working, already-tested machinery for a temporarily-zero call count is
exactly the kind of premature cut this project's own guidance argues
against when a real, if inactive, use case still exists.

Porting hermes's static-fallback-summary path (a deterministic,
non-LLM summary built from raw turn metadata when the real
summarization call fails): rejected for this item's scope — this
project's reactive-only, single-conversation-loop scale doesn't yet
justify a second, non-LLM compaction mechanism; a terminal failure simply
leaves the conversation unhandled, the same shape `CONTEXT_OVERFLOW_UNHANDLED`
already has today.

Having `context.turn_complete` construct the summary `Message` itself:
rejected — would require a real runtime import of `conversation.py`'s
`Message` class, breaking the import-cycle invariant C10 established.
Returning a plain string and letting `conversation.py`'s own `run_turn`
build the `Message` costs nothing and keeps the invariant intact.

Content-sniffing a message's text for a summary marker instead of adding
`Message.is_summary`: rejected on the same grounds C10 already used
against re-parsing a concatenated prompt for its stable-prefix boundary —
a marker can legitimately recur inside real conversation content; storing
the fact explicitly, once, avoids the whole class of ambiguity.

Duplicating `complete()`'s retry loop inside `context.turn_complete`
rather than extracting `model_access.resolve()`: considered as the
smaller diff. Rejected because it is the literal duplication this
project's own guidelines argue against — the same loop, the same bound,
copied instead of shared, and any future fix to the retry mechanics would
need to land twice.

## Concerns

**The widest tension in this design is the same one C10 already named,
now larger**: this item reopens `conversation.py` (a `Message` field,
two new functions, a reordered loop section, a refactored `complete()`),
`model_access.py` (a new function), and adds two new files. The
alternative — leaving `turn_complete`/`after_tool_result` real-but-narrow
and accepting that compaction can never actually shrink history, only
rewrite a string nothing needed rewritten — is smaller but does not
satisfy the intent at all; a narrower diff that doesn't solve the stated
problem isn't a real alternative. Flagging for the reviewer to look at
hardest: the `complete()` refactor, since it is the one change to
already-shipped, already-tested behavior with no new feature riding on
it — its own test suite passing unchanged is the whole proof it's safe.

**`TurnResult.appended`'s meaning weakens across a mid-turn compaction
event.** `turn_start_seq` is captured once, in PROLOGUE, before a
possible mid-turn compaction can shrink `messages` out from under it —
`range(turn_start_seq, len(messages))` could then start past its own end.
Resolved by clamping `turn_start_seq` to `min(turn_start_seq,
len(messages))` after any compaction, accepted as an approximation for
the rare case a single turn both overflows and gets compacted — checked
that only one existing test reads `.appended` at all
(`tests/unit/test_conversation.py:634`), and it doesn't exercise this
path, so nothing regresses; not fixed further, since nothing today reads
`appended` for anything beyond that one length check.

**A candidate CLAUDE.md amendment**, surfaced the same way C10's
`mark_cache_boundary` placement was: *a conversation's message history is
mutated only through `conversation.append`/`conversation.repair`/`conversation.compact`
— never by direct list or tuple mutation elsewhere* (extending the
existing rule's named set, not replacing it) — flagged for the user to
approve before build. A second, smaller candidate: *a retry loop over a
provider's `Retry` outcome lives once, in MODEL-ACCESS's own `resolve()`,
never duplicated per caller* — also flagged.
