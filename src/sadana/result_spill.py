"""A tool result too bulky to keep in a conversation is saved in full to a
plain file, with a short reference left behind instead — CONTEXT's
after-tool-result checkpoint (`docs/tasks/C11-context-completion/spec.md`).

The only piece of this project's own result-spilling design that isn't a
non-goal: no remote-sandbox path translation, no database, no scheduled
pruning (`docs/tasks/C11-context-completion/spec.md`'s own Open questions
names why). A write failure never raises — it falls back to returning the
original content unchanged, so the caller's own existing truncation stays
the safety net it already was.

A new file, not a section of `context.py`: this is the one place in that
checkpoint's design that touches real disk I/O — CLAUDE.md's own rule that
a module touching real I/O is its own file, regardless of line count.
"""

from __future__ import annotations

from pathlib import Path

from sadana import config

_PREVIEW_CHARS = 1500


def _spill_dir() -> Path:
    directory = config.get_paths().state_dir / "tool_results"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _spill_path(tool_call_id: str) -> Path:
    """A filesystem-safe name derived from `tool_call_id`. Falls back to a
    fixed name when sanitizing empties it out — two spills landing on the
    same fallback name just overwrite each other, no worse than losing the
    id entirely."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in tool_call_id)
    return _spill_dir() / f"{safe or 'result'}.txt"


def write_and_reference(tool_call_id: str, content: str) -> str:
    """Writes `content` to disk and returns a short in-context reference
    describing where it went, its size, and a preview. Never raises: a
    write failure returns `content` unchanged, so the caller's own
    existing truncation is the fallback, not a crash."""
    try:
        path = _spill_path(tool_call_id)
        path.write_text(content, encoding="utf-8", errors="replace")
    except OSError:
        return content

    preview = content[:_PREVIEW_CHARS]
    return (
        f"[full result saved to {path} — {len(content)} chars. No tool exists yet "
        f"to re-read it; this note and the preview below are all that's in context.]\n\n{preview}"
    )
