"""The Bash hook's path extraction, executed rather than read.

`pathre` in `.claude/hooks/gate-artifact-chain.sh` is nested-quoted bash that
no Python test can see, and it fails open: a typo stops it gating and nothing
goes red. So it is run here as a subprocess against a throwaway project
directory, which is the only tier that can say anything about it at all.

The word "approved" appears below as *data*, written into a fixture under
``tmp_path`` so the gate has an input to read. See
``tests/unit/test_artifact_gate.py``'s docstring for why that is the one place
CLAUDE.md's rule does not reach.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "gate-artifact-chain.sh"
# Built at runtime on purpose. Spelled out, a redirect operator followed by a
# path under tests/ would trip the very hook this file tests, the moment an
# agent writes this file from a shell heredoc. That happened while writing it.
REDIRECT = ">"
FILLER = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"


def _project(tmp_path: Path, files: str) -> Path:
    """A throwaway project directory the hook can `cd` into."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "artifact.py", tmp_path / "scripts" / "artifact.py")
    d = tmp_path / "docs" / "tasks" / "T1-thing"
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        f"# Plan: T (from intent.md 2026-09-14)\n\nAuthor: A (role). Status: approved.\n\n"
        f"## Files that change\n\n{files}\n\n"
        f"## Order of work\n\n1. {FILLER}\n\n## Risks\n\n{FILLER} sixteen\n\n## Proof\n\n{FILLER}\n"
    )
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "active-task").write_text(str(d))
    return tmp_path


def _hook(project: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
        capture_output=True,
        text=True,
    )


def test_a_heredoc_into_tests_reaches_the_gate(tmp_path: Path) -> None:
    """The hole this work item closed: before, only src/ and docs/tasks/ matched."""
    project = _project(tmp_path, "src/sadana/listed.py")
    r = _hook(project, f"cat {REDIRECT} tests/unit/test_smuggled.py <<EOF")
    assert r.returncode == 2
    assert "does not name tests/unit/test_smuggled.py" in r.stderr


def test_a_heredoc_into_a_named_test_is_allowed(tmp_path: Path) -> None:
    project = _project(tmp_path, "tests/unit/test_named.py")
    r = _hook(project, f"cat {REDIRECT} tests/unit/test_named.py <<EOF")
    assert r.returncode == 0, r.stderr


def test_a_read_only_command_naming_a_test_path_is_not_gated(tmp_path: Path) -> None:
    """The destination anchor: a test path as an *argument* is not a write."""
    project = _project(tmp_path, "src/sadana/listed.py")
    r = _hook(project, f"grep -rn foo tests/unit/ {REDIRECT} /tmp/out")
    assert r.returncode == 0, r.stderr


def test_a_python_open_for_reading_is_not_a_write(tmp_path: Path) -> None:
    """The destination is the path inside `open(..., 'w')`, not any path in the
    command. This one writes to /tmp and only *reads* the test file."""
    project = _project(tmp_path, "src/sadana/listed.py")
    reader = "open('tests/unit/test_a.py').read()"
    cmd = f"python3 -c \"open('/tmp/x','w').write({reader})\""
    r = _hook(project, cmd)
    assert r.returncode == 0, r.stderr


def test_a_python_open_for_writing_into_tests_reaches_the_gate(tmp_path: Path) -> None:
    """The other half of the same rule, and a real hole while it was missing:
    an unanchored fallback narrow enough to avoid the read case above let this
    through, so `tests/` was writable from a shell command with no plan entry."""
    project = _project(tmp_path, "src/sadana/listed.py")
    r = _hook(project, "python3 -c \"open('tests/unit/smuggled.py','w').write('x')\"")
    assert r.returncode == 2
    assert "does not name tests/unit/smuggled.py" in r.stderr


def test_every_destination_is_gated_not_only_the_last(tmp_path: Path) -> None:
    """`tail -1` collapsed a multi-write command to one target, so putting a
    plan-listed path last waved every unlisted file before it through."""
    project = _project(tmp_path, "src/sadana/listed.py")
    cmd = f"cat {REDIRECT} src/sadana/unlisted.py <<E1\nx\nE1\n" f"cat {REDIRECT} src/sadana/listed.py <<E2\ny\nE2"
    r = _hook(project, cmd)
    assert r.returncode == 2
    assert "does not name src/sadana/unlisted.py" in r.stderr
