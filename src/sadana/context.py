"""CONTEXT's lifecycle interface — the four checkpoints
``docs/tasks/B2-cycle-contract/spec.md`` fixed, called only by CONVERSATION,
never given a reference to its caller's loop state.

Two of the four have real behavior here (this work item's own scope, see
``docs/tasks/C10-context-lifecycle/spec.md``): ``after_response`` accumulates
usage into ``ContextState``; ``before_send`` decides the cache-boundary hint
(what actually lands on the wire is MODEL-ACCESS's job, not this module's —
see CLAUDE.md's rule on provider wire-format knowledge). ``after_tool_result``
and ``turn_complete`` are real call sites that stay honest no-ops (identity /
``None``) — real result-spilling and real compaction are named follow-up
work, not built here.

Only ``after_response`` and ``turn_complete`` may write ``ContextState``, per
B2's own rule: an aborted turn cannot drift the accounting, and a retry that
calls ``before_send`` twice in one turn cannot double-count anything, because
``before_send`` never writes.

No runtime import of ``conversation.py`` or ``model_access.py`` — ``Message``
and ``Usage`` are referenced only under ``TYPE_CHECKING``, so nothing here
ever risks an import cycle with either module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sadana import config

if TYPE_CHECKING:
    from sadana.conversation import Message
    from sadana.model_access import Usage


@dataclass(frozen=True)
class ContextState:
    """A conversation's own running account — created once when the
    conversation begins, lives across every turn, discarded when the
    conversation ends. Not persisted (spec.md's own requirement 7)."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


@dataclass(frozen=True)
class CacheHint:
    """What ``before_send`` decided about the cache boundary. Data only —
    MODEL-ACCESS's ``mark_cache_boundary`` is the one place that turns this
    into an actual wire-format marker."""

    stable_prefix_len: int
    trailing_marks: int


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


def after_response(state: ContextState, usage: Usage) -> ContextState:
    """Folds one completed exchange's real usage figures into the running
    total. The only checkpoint, besides ``turn_complete``, allowed to
    write ``ContextState``."""
    return ContextState(
        total_prompt_tokens=state.total_prompt_tokens + usage.prompt_tokens,
        total_completion_tokens=state.total_completion_tokens + usage.completion_tokens,
    )


def after_tool_result(state: ContextState, result: Message) -> Message:
    """Identity today. Real call site for a future result-spilling item —
    named as a non-goal of this one, not built here."""
    del state
    return result


def turn_complete(
    state: ContextState,
    history: tuple[Message, ...],
    system_prompt: str,
) -> tuple[ContextState, str | None]:
    """``None`` today carries the same meaning the removed ``compress``
    callback's ``None`` already had: "not compressed". Real compaction is a
    named non-goal of this work item, not built here."""
    del history, system_prompt
    return state, None
