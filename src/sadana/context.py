"""CONTEXT's lifecycle interface — the four checkpoints
``docs/tasks/B2-cycle-contract/spec.md`` fixed, called only by CONVERSATION,
never given a reference to its caller's loop state.

All four have real behavior now. C10 (`docs/tasks/C10-context-lifecycle/spec.md`)
built ``after_response`` (usage accounting) and ``before_send`` (the
cache-boundary hint — what actually lands on the wire is MODEL-ACCESS's job,
not this module's, per CLAUDE.md's rule on provider wire-format knowledge).
C11 (`docs/tasks/C11-context-completion/spec.md`) builds the other two:
``turn_complete`` performs real compaction (a genuine second model call
summarizing the portion of history being dropped), and ``after_tool_result``
spills an oversized tool result to disk, leaving a short reference behind.

Only ``after_response`` and ``turn_complete`` may write ``ContextState``, per
B2's own rule: an aborted turn cannot drift the accounting, and a retry that
calls ``before_send`` twice in one turn cannot double-count anything, because
``before_send`` never writes.

No runtime import of ``conversation.py`` — ``Message`` is referenced only
under ``TYPE_CHECKING``, so nothing here ever risks a cycle with it (it
imports this module). ``model_access`` and the new ``result_spill`` module
are real, non-cyclic dependencies: neither imports ``context.py`` or
``conversation.py`` back."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sadana import config, model_access, result_spill

if TYPE_CHECKING:
    from sadana.conversation import Message


@dataclass(frozen=True)
class ContextState:
    """A conversation's own running account — created once when the
    conversation begins, lives across every turn. This module never
    persists it directly (spec.md's own requirement 7 — CONTEXT has no
    disk/network access); ``conversation_store.py``'s ``create()``/``save()``/
    ``load()`` do that at the SESSION-STORE seam instead, so its values
    (unlike the dataclass instance itself) do survive a save/reload round
    trip (docs/tasks/C12-context-resume-round-trip/spec.md)."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


@dataclass(frozen=True)
class CacheHint:
    """What ``before_send`` decided about the cache boundary. Data only —
    MODEL-ACCESS's ``mark_cache_boundary`` is the one place that turns this
    into an actual wire-format marker."""

    stable_prefix_len: int
    trailing_marks: int


@dataclass(frozen=True)
class TurnCompleteResult:
    """``turn_complete``'s return value. Both fields are independently
    optional, on purpose — a system-prompt rewrite and a history
    compaction are decoupled mechanisms with different lifecycles (every
    production harness checked keeps them separate), not a forced either/or.
    Both ``None`` carries the same meaning the removed callback's bare
    ``None`` already had: "not compressed". ``new_system_prompt`` stays
    unused by this module's own real behavior — compaction never touches
    the system prompt — but the field exists so a real future need for it
    doesn't reopen this type a second time; `rotate_prompt()`/
    `PromptRotationReason` (C7/C8) stay real, available machinery for
    exactly that case."""

    context_state: ContextState
    new_system_prompt: str | None = None
    new_summary_text: str | None = None


def trailing_marks_from_config() -> int:
    """How many of the most recent eligible messages ``before_send`` asks
    to be marked, alongside the system prompt's stable prefix. The one
    adjustable policy this work item ships — see spec.md's Rejected
    alternatives on why this is a config int, not a strategy object."""
    return config.env_int("SADANA_CONTEXT_CACHE_TRAILING_MARKS", 1)


def before_send(history: tuple[Message, ...], stable_prompt_len: int) -> CacheHint:
    """Read-only against ``ContextState`` — takes none, per B2's rule.
    May be called more than once per turn (e.g. a retry after
    ``needs-context-compression``) with no side effects.

    Returns a bare ``CacheHint``, not a richer ``history``/``tool_specs``/
    ``cache_hint`` bundle: B2 names reshaping history and contributing
    tool schemas as things this checkpoint *may* also return, but nothing
    consumes either here — real compaction and CONTEXT-contributed tools
    are both non-goals of this work item (self-check caught the unused
    fields; widening the return type is an addition for whichever item
    makes either real, not a rewrite of this one).

    ``history`` is accepted for the same forward-looking reason — a real
    compaction policy would need it — even though this function does not
    read it yet.

    B2 also names the active model's context window size as available to
    this checkpoint — deliberately not a parameter here: nothing in this
    work item's real policy consumes it (real compaction is a named
    non-goal), and `model_access.context_window()` only resolves the one
    model this project has actually wired, so calling it unconditionally
    for every turn would raise `KeyError` for every other provider/model —
    including every test double in this codebase. A follow-up item that
    builds a real policy needing it can add the parameter back alongside
    that policy, not before."""
    del history
    return CacheHint(stable_prefix_len=stable_prompt_len, trailing_marks=trailing_marks_from_config())


def after_response(state: ContextState, usage: model_access.Usage) -> ContextState:
    """Folds one completed exchange's real usage figures into the running
    total. The only checkpoint, besides ``turn_complete``, allowed to
    write ``ContextState``."""
    return ContextState(
        total_prompt_tokens=state.total_prompt_tokens + usage.prompt_tokens,
        total_completion_tokens=state.total_completion_tokens + usage.completion_tokens,
    )


def result_spill_threshold_from_config() -> int:
    """The char count above which a tool result gets spilled to disk
    instead of kept inline. Defaults to the same 100,000 chars
    `SADANA_CONVERSATION_TOOL_RESULT_CHARS` already defaults to, so
    spilling handles everything that would otherwise be silently
    truncated — that cap becomes a pure safety net once this exists."""
    return config.env_int("SADANA_CONTEXT_RESULT_SPILL_CHARS", 100_000)


async def after_tool_result(state: ContextState, result: Message) -> Message:
    """Identity at or under the spill threshold. Above it, the full
    result is saved to disk (`result_spill.write_and_reference`,
    `asyncio.to_thread`-wrapped — matches `conversation_store.py`'s own
    precedent for wrapping even local disk I/O) and replaced with a short
    reference. ``state`` isn't read or written — spilling isn't part of
    the running account `after_response`/`turn_complete` keep."""
    del state
    content = result.content or ""
    if len(content) <= result_spill_threshold_from_config():
        return result
    reference = await asyncio.to_thread(result_spill.write_and_reference, result.tool_call_id or "result", content)
    # dataclasses.replace on an EXISTING Message needs no import of the
    # class itself, unlike constructing a brand-new one (see
    # turn_complete's own docstring on why it never does that).
    return replace(result, content=reference)


def compaction_tail_messages_from_config() -> int:
    """How many of the most recent messages a compaction event must never
    touch. The one policy number this checkpoint owns, matching
    `trailing_marks_from_config()`'s own precedent — the *mechanics* of
    finding a safe cut at or before this many messages back is
    CONVERSATION's job (`find_compaction_boundary`), not this module's."""
    return config.env_int("SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES", 10)


def _render_for_summary(message: Message) -> str:
    """One line of plain text for one message, for a summarization
    prompt only — never stored, never shown to a user, just what the
    summarizing model reads."""
    line = f"{message.role}: {message.content or ''}"
    for call in message.tool_calls:
        fn = call.get("function") or {}
        line += f" [called {fn.get('name', '?')}]"
    return line


def _build_summarization_prompt(old_portion: tuple[Message, ...], system_prompt: str) -> str:
    """Two framings, not one: refining an existing summary is a different
    ask than summarizing raw conversation for the first time — asking a
    model to "summarize" its own prior summary as if it were fresh
    conversation is exactly the summary-of-a-summary degradation this
    checkpoint exists to avoid (spec.md's own reference-corpus finding)."""
    refining = bool(old_portion) and old_portion[0].is_summary
    rendered = "\n".join(_render_for_summary(m) for m in old_portion)
    if refining:
        instruction = (
            "The first entry below is your own summary of an even-earlier part "
            "of this conversation. Produce an updated summary that folds in "
            "everything that happened since, without re-describing what the "
            "existing summary already covers as if it were new."
        )
    else:
        instruction = (
            "Summarize the conversation below so it can continue without this "
            "part in view. Keep what's still relevant to finishing the task; "
            "drop what's resolved or no longer needed."
        )
    return (
        f"{instruction}\n\n"
        f"Conversation context (system prompt): {system_prompt}\n\n"
        f"--- conversation to summarize ---\n{rendered}\n--- end ---"
    )


async def turn_complete(
    state: ContextState,
    history: tuple[Message, ...],
    system_prompt: str,
    tail_start: int,
    provider: str,
    model: str,
) -> TurnCompleteResult:
    """Real compaction: summarizes `history[:tail_start]` via a genuine
    second model call — B2's own framing, "just another caller of
    `send()`". `tail_start` is a boundary CONVERSATION's own
    `find_compaction_boundary` already computed; this function trusts it
    and never decides where it is safe to cut.

    Returns `new_summary_text=None` (both fields `None`, since compaction
    never touches `system_prompt`) on any failure — an empty-content
    response, or any outcome besides a real `Response` — leaving the
    conversation unhandled exactly as today, with no static fallback
    summary (spec.md's own Rejected alternatives: this project's scale
    doesn't justify a second, non-LLM compaction mechanism yet).

    Never constructs a `Message` — returns plain text; `conversation.py`'s
    own `compact()` builds the actual summary message, so this module
    never needs a real, cycle-creating import of `Message`."""
    old_portion = history[:tail_start]
    if not old_portion:
        return TurnCompleteResult(context_state=state)

    prompt = _build_summarization_prompt(old_portion, system_prompt)
    request = model_access.Request(messages=({"role": "user", "content": prompt},), provider=provider, model=model)
    outcome = await model_access.resolve(request)

    if not isinstance(outcome, model_access.Response) or not outcome.content:
        return TurnCompleteResult(context_state=state)
    return TurnCompleteResult(context_state=state, new_summary_text=outcome.content)
