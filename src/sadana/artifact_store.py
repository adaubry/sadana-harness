"""Where a plugin run puts what it makes.

`docs/tasks/ARTIFACT-STORE-01-somewhere-to-put-what-a-plugin-makes/spec.md`.
`Artifact.ref` has been documented as "a path under the run's own output
directory" since G2, which named creating one as its own non-goal; this is
that directory.

Its own file rather than part of `plugins.py`: every function here touches
the disk or the ambient run, and CLAUDE.md keeps an I/O module separate from
a block's pure-function module.

The layout is `<state_dir>/artifacts/<conversation>/<turn_seq>/<seq_in_turn>`
— the key `observability.plugin_runs` already uses as its primary key, so the
files a run produced and the record of it running line up by reading either
(CLAUDE.md: "A derived/observability record's key reuses whatever unique key
its producing call already minted"). Nothing here mints an identifier.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from sadana import config

_UNSAFE = re.compile(r"[^a-z0-9._-]+")

# The directory of the run currently walking, or None when no run is current.
# A ContextVar rather than a module global: under the gateway two conversations
# can be in flight at once, and a global would let one write into the other's
# directory. Set only by `activate()`, which always restores what it replaced.
_ACTIVE: contextvars.ContextVar[Path | None] = contextvars.ContextVar("sadana_artifact_dir", default=None)


class NoOutputDirectory(Exception):
    """`output_dir()` was called with no run current, or by a run that was
    given none. Raised rather than returning a guessed path: a body that
    asks out of turn must not quietly write somewhere arbitrary."""


def run_dir_name(conversation_key: str) -> str:
    """One path-safe directory name for a conversation.

    The key is *encoded*, not validated. `sadana chat --key NAME` takes an
    arbitrary string, so existing conversations are free to be named with a
    slash, a `..` or a leading dash, and refusing those here would break
    conversations that already work.

    The trailing digest is not a second identity for the conversation
    (CLAUDE.md's rule against minting one still holds — this is a pure
    function of the key, computed fresh, stored nowhere). It is what stops two
    keys the encoding flattens together — `a/b` and `a-b` — landing in one
    directory. The readable half is kept because a person reads this path out
    of a plugin's own answer.
    """
    digest = hashlib.sha256(conversation_key.encode("utf-8")).hexdigest()[:8]
    slug = _UNSAFE.sub("-", conversation_key.lower()).strip("-.")[:48]
    return f"{slug}-{digest}" if slug else digest


def run_dir(state_dir: Path, conversation_key: str, turn_seq: int, seq_in_turn: int) -> Path:
    """Where that run's files belong. Pure path arithmetic — creates nothing,
    so asking is free and a run that produces nothing leaves nothing."""
    return state_dir / "artifacts" / run_dir_name(conversation_key) / str(turn_seq) / str(seq_in_turn)


def for_run(conversation_key: str, turn_seq: int, seq_in_turn: int) -> Path:
    """`run_dir` against the live state directory, resolved fresh on every
    call — never captured at import, the same posture `config.get_paths()`
    and `plugins._plugins_root()` already take, and what keeps this correct
    under a test that redirects the state directory."""
    return run_dir(config.get_paths().state_dir, conversation_key, turn_seq, seq_in_turn)


@contextlib.contextmanager
def activate(directory: Path | None) -> Iterator[None]:
    """Make `directory` the current run's output directory for the duration.

    A context manager with no non-contextual entry point on purpose: the
    failure this shape prevents is silent. A leaked activation does not raise
    later — it makes `output_dir()` *succeed* in a run that should have had
    none, and something writes into a stale directory with nothing to notice
    it. Restoring on the way out, exception or not, is the whole job.
    """
    token = _ACTIVE.set(directory)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def output_dir() -> Path:
    """The current run's directory, created on first call.

    This is what a node body calls. It is the only way in: a body is never
    handed the path as an argument, so there is no signature to thread it
    through and no value in `arguments` for a model to influence.
    """
    directory = _ACTIVE.get()
    if directory is None:
        raise NoOutputDirectory("no plugin run is current, or this run was given no output directory")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def contains(directory: Path, candidate: str) -> bool:
    """Whether `candidate` really lands inside `directory`.

    Resolved on both sides, so a symlink inside the directory pointing out of
    it answers false — the check the allowlist half cannot make (CLAUDE.md:
    the pattern and the post-resolution containment check, never either).

    Strictly inside: the directory is not a file in itself, so a `ref` naming
    the directory answers false rather than being recorded as a file that is
    really a folder.

    `ValueError` is caught alongside `OSError` because `Path("a\0b").resolve()`
    raises it on a perfectly legal `str` — a predicate returning `bool` must
    not raise on input it is being asked to judge.
    """
    try:
        resolved = Path(candidate).resolve()
        root = directory.resolve()
    except (OSError, ValueError):
        return False
    return resolved != root and resolved.is_relative_to(root)


def contains_active(candidate: str) -> bool:
    """`contains()` against the current run's directory, false when there is
    none.

    The predicate belongs here rather than at the caller: this module owns
    the ambient directory, so asking it "does this land in the active one"
    keeps `run_graph` from re-deriving an answer out of a parameter that is
    the same value by construction.
    """
    directory = _ACTIVE.get()
    return directory is not None and contains(directory, candidate)
