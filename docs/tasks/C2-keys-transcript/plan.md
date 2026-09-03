# Plan: A conversation's history that cannot become malformed (from intent.md 2026-09-03)

## Files that change

- `src/sadana/conversation.py` (new) — `ConversationKey`, `MessageKey`,
  `Message`, `TranscriptInvariantError`, `pending_tool_call_ids()`,
  `append()`, `repair()`.
- `tests/unit/test_conversation.py` (new) — one test per acceptance
  criterion in `spec.md`, mirroring `tests/unit/test_model_access.py`'s
  style: `@pytest.mark.unit` per test, `# ── section ──` separators, plain
  dataclass construction, no fixtures needed (the module does no I/O, so
  none of `conftest.py`'s state-dir isolation applies — it still runs under
  it harmlessly).

## Design recap (from spec.md, restated here so this plan stands alone)

```python
ConversationKey = str  # caller-supplied, e.g. "support/ticket-4821"

@dataclass(frozen=True)
class MessageKey:
    conversation: ConversationKey
    msg_seq: int

@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[dict, ...] = ()   # same shape as model_access.Response.tool_calls
    tool_call_id: str | None = None     # only set when role == "tool"

class TranscriptInvariantError(Exception): ...

def pending_tool_call_ids(messages: tuple[Message, ...]) -> frozenset[str]: ...
def append(conversation: ConversationKey, messages: tuple[Message, ...], message: Message) -> tuple[tuple[Message, ...], MessageKey]: ...
def repair(messages: tuple[Message, ...]) -> tuple[Message, ...]: ...
```

`pending_tool_call_ids` finds the index of the **last** message that is an
`assistant` message with non-empty `tool_calls`. If there is none, it
returns `frozenset()`. Otherwise it returns `{tc["id"] for tc in that
message.tool_calls} - {m.tool_call_id for m in messages[index+1:] if
m.role == "tool"}` — the scan for already-paired ids is bounded to messages
*after* that specific assistant message, not the whole history, because a
provider can reuse a short id string (e.g. `"call_1"`) across separate tool
rounds; a global-uniqueness scan would falsely treat an earlier round's id
as already paired for a later round that reused it.

`append` computes `pending = pending_tool_call_ids(messages)` first:

- `pending` non-empty and `message.role == "tool"` and `message.tool_call_id
  in pending` → the only accepted case: return `messages + (message,)` and
  its `MessageKey`.
- `pending` non-empty, anything else → raise `TranscriptInvariantError`
  (role-alternation violation — something tried to move on while a tool
  call was still unanswered, or a tool row named an id nothing asked for).
- `pending` empty and `message.role == "tool"` → raise
  `TranscriptInvariantError` (nothing to pair against).
- `pending` empty, `message.role in ("user", "assistant")` → accepted
  unconditionally (this is also how a fresh conversation's first message is
  appended, and how a text-only assistant turn or a new tool_calls batch is
  appended).

`repair` computes `pending = pending_tool_call_ids(messages)`. If empty,
returns `messages` unchanged (same object, not a copy — cheap to prove with
`is`). Otherwise, for each id in `pending`, **in the order they appear in
the triggering assistant message's `tool_calls`**, appends one `Message(
role="tool", content="[repaired] interrupted before execution",
tool_call_id=id)`. Returns the extended tuple. After `repair`,
`pending_tool_call_ids` on the result is always `frozenset()`.

## Order of work

1. **Write `src/sadana/conversation.py`.** Module docstring names the
   contract it implements (mirroring `model_access.py`'s opening docstring
   style: what this module is the single source of truth for, and what
   `spec.md` it traces to). Types first, then the three functions. No
   `__all__` needed — `model_access.py` doesn't use one either; matching
   the existing convention.
2. **Write `tests/unit/test_conversation.py`**, one test per `spec.md`
   acceptance criterion, plus the cross-round id-reuse case named below as
   the risky step's regression test. Run `make test` scoped to this file
   only first (fast inner loop), then the full narrow check.
3. **Run `make verify`** and paste its output as this stage's evidence.

Tests land in the same step as the code they check (not after), since
there's no reason to let step 1 exist unverified even briefly in a two-file
change this size.

## Risks

**What could this change break?** Nothing. No file in `src/sadana/`
currently imports `conversation` (it doesn't exist yet), so there are no
existing callers to disturb. The only shared fixture in play is
`tests/conftest.py`'s autouse state-dir-isolation fixture — this module
touches no filesystem, network, or clock, so that fixture is inert for it,
not a risk.

**Which step is riskiest?** Step 1's `pending_tool_call_ids` scan boundary.
Scanning the *whole* history for already-used tool_call ids instead of only
the tail after the triggering assistant message would silently misclassify
a reused id from an earlier tool round as "already paired," letting a
malformed append through undetected — exactly the failure this work item
exists to prevent, reintroduced by an off-by-scope bug in the one function
that's supposed to catch it. Mitigation: the test list includes two
sequential tool rounds in one conversation that reuse the same id string,
asserting the second round's pairing is judged independently of the
first's. This step comes first (nothing else in the file depends on
getting anything else right first), so if it needs rework nothing built on
top of it has to be redone.

**Is this drifting back toward anything `spec.md` already rejected?** No —
checked against all four Rejected Alternatives entries: this plan defines
no `Transcript` class or protocol (pure functions only), no package split
(one file), no second `ToolCall` type (`tool_calls` stays `tuple[dict,
...]`), no stored sequence counter (`MessageKey.msg_seq` is computed from
`len(new_messages) - 1` at the `append` call site, never incremented or
stored anywhere).

## Proof

`tests/unit/test_conversation.py` covers, at minimum, one test per
`spec.md` acceptance-criteria checkbox:

- `append` raises `TranscriptInvariantError` for a `tool` message with no
  pending ids at all (fresh/empty history).
- `append` raises for a `tool` message whose id isn't in the current
  pending set (something is pending, but not this one).
- `append` raises for a `user` message appended while pending ids remain.
- `append` raises for an `assistant` message (with or without new
  `tool_calls`) appended while pending ids remain.
- `append` accepts an `assistant` message with `tool_calls=()` when nothing
  is pending.
- `append` accepts a `tool` message matching a pending id, and the returned
  tuple's `pending_tool_call_ids` no longer contains that id.
- Two sequential tool rounds that reuse the same id string are paired
  independently (the risky-step regression case).
- `repair` returns its input unchanged (`is`, not just `==`) when nothing
  is pending.
- `repair` appends exactly one `tool` message per pending id, in
  declaration order, and `pending_tool_call_ids` of the result is
  `frozenset()`.
- Both `append` (success and raise paths) and `repair` leave the original
  `messages` tuple passed in untouched — asserted by identity/length on the
  original argument after the call, not just by re-deriving pending ids.

`make verify` run at the end, pasted in full, ending `VERIFY OK`, is this
stage's evidence — not "tests pass" on its own.
