"""The chain gate: approval, ownership, and the commit check.

Why the literal word "approved" appears all over this file: it is *data*.
Every occurrence is written into a throwaway artifact under ``tmp_path`` in
order to give ``cmd_gate`` the input it reads. CLAUDE.md's rule is that an
agent never writes that word into an artifact — the word is the user's, and
it is the approval. A test that exercises the gate has to manufacture both
sides of that check, and a file under ``tmp_path`` is the one place where
doing so cannot be mistaken for an approval of anything.

The gate resolves ``docs/tasks`` and ``.claude/active-task`` relative to the
process's working directory, so these tests use ``monkeypatch.chdir`` — which
pytest restores on teardown. That is a real, named cost: this file is not
safe under ``pytest-xdist``. See ``spec.md § Concerns`` for why that was
preferred to widening ``cmd_gate``'s signature for the benefit of its test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.artifact import BY_ARTIFACT, cmd_gate, cmd_status, planned_paths, status_of, validate

pytestmark = pytest.mark.unit

# Sixteen words: clears the trace minimum of every stage (10, 12, 15).
FILLER = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"


def _intent(status: str) -> str:
    return (
        f"# Intent: T\n\nAuthor: A (role). Status: {status}.\n\n"
        f"## Problem\n\n{FILLER}\n\n"
        f"## Proposed outcome\n\n{FILLER}\n\n"
        f"## Affected users and systems\n\n{FILLER}\n\n"
        f"## Constraints\n\n{FILLER}\n\n"
        f"## Changed during planning\n\n{FILLER}\n"
    )


def _spec(status: str) -> str:
    return (
        f"# Spec: T\n\nIntent: docs/tasks/T1-thing/intent.md\n\nAuthor: A (role). Status: {status}.\n\n"
        f"## Requirements\n\n{FILLER}\n\n"
        f"## Design\n\n{FILLER}\n\n"
        f"## Interface\n\n{FILLER}\n\n"
        f"## Acceptance criteria\n\n{FILLER}\n\n"
        f"## Rejected alternatives\n\n{FILLER}\n\n"
        f"## Concerns\n\n{FILLER}\n"
    )


def _review() -> str:
    return f"# Review: T\n\n## Evidence\n\n```\nVERIFY OK\n```\n\n## Findings\n\n{FILLER}\n\n## Decision\n\n{FILLER}\n"


def _plan(status: str = "draft", files: str = "src/sadana/x.py") -> str:
    return (
        f"# Plan: T (from intent.md 2026-09-14)\n\nAuthor: A (role). Status: {status}.\n\n"
        f"## Files that change\n\n{files}\n\n"
        f"## Order of work\n\n1. {FILLER}\n\n"
        f"## Risks\n\n{FILLER}\n\n"
        f"## Proof\n\n{FILLER}\n"
    )


@pytest.fixture
def item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty work item, active, with the gate's cwd pointed at it."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "docs" / "tasks" / "T1-thing"
    d.mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "active-task").write_text(str(d))
    return d


def rel(d: Path, name: str) -> str:
    """The repo-relative path the hook would hand to the gate."""
    return f"docs/tasks/{d.name}/{name}"


# ── status_of ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("written", "read_back"),
    [("draft", "draft"), ("approved", "approved"), ("APPROVED", "approved")],
    ids=["draft", "trailing-period", "uppercase"],
)
def test_status_of_reads_the_word(tmp_path: Path, written: str, read_back: str) -> None:
    p = tmp_path / "intent.md"
    p.write_text(_intent(written))  # the template always adds a trailing "."
    assert status_of(p) == read_back


def test_status_of_is_empty_without_a_header_line(tmp_path: Path) -> None:
    p = tmp_path / "intent.md"
    p.write_text("# Intent: T\n\n## Problem\n\nnothing here\n")
    assert status_of(p) == ""
    assert status_of(tmp_path / "absent.md") == ""


# ── planned_paths ──────────────────────────────────────────────────────────
def test_planned_paths_parses_the_comma_separated_form() -> None:
    text = _plan(files="path/one.py (new), path/two.py, tests/test_two.py")
    assert planned_paths(text) == {"path/one.py", "path/two.py", "tests/test_two.py"}


def test_planned_paths_parses_the_bulleted_form() -> None:
    files = "- `src/sadana/a.py` (new) — does a thing\n- `tests/unit/test_a.py` — covers it\n"
    assert planned_paths(_plan(files=files)) == {"src/sadana/a.py", "tests/unit/test_a.py"}


def test_planned_paths_finds_a_path_on_a_wrapped_continuation_line() -> None:
    """The reason this is a token scan and not a layout parser."""
    files = "- `src/sadana/a.py` — and while there,\n  `tests/unit/test_a.py` gains two cases.\n"
    assert planned_paths(_plan(files=files)) == {"src/sadana/a.py", "tests/unit/test_a.py"}


def test_planned_paths_keeps_a_glob_and_expands_a_directory() -> None:
    files = "src/sadana/console/*, tests/fixtures/plugins/plugin-d/"
    assert planned_paths(_plan(files=files)) == {"src/sadana/console/*", "tests/fixtures/plugins/plugin-d/*"}


def test_planned_paths_keeps_a_dotfile_and_drops_a_leading_dot_slash() -> None:
    files = "`./src/sadana/a.py`, `.claude/hooks/gate.sh`"
    assert planned_paths(_plan(files=files)) == {"src/sadana/a.py", ".claude/hooks/gate.sh"}


def test_planned_paths_is_empty_without_the_section() -> None:
    assert planned_paths("# Plan: T\n\n## Order of work\n\n1. go\n") == set()


# ── the artifact branch: the previous stage must be approved ───────────────
def test_next_artifact_is_refused_while_the_previous_stage_is_draft(item: Path, capsys) -> None:
    (item / "intent.md").write_text(_intent("draft"))
    assert cmd_gate("write", rel(item, "spec.md")) == 2
    err = capsys.readouterr().err
    assert "intent.md is not approved" in err
    assert "Status is 'draft'" in err
    assert "Do not write that word yourself." in err


def test_next_artifact_is_allowed_once_the_previous_stage_is_approved(item: Path) -> None:
    (item / "intent.md").write_text(_intent("approved"))
    assert cmd_gate("write", rel(item, "spec.md")) == 0


def test_the_first_stage_has_no_previous_stage_to_approve(item: Path) -> None:
    assert cmd_gate("write", rel(item, "intent.md")) == 0


# ── the source branch: approval, then ownership ────────────────────────────
def test_source_write_is_refused_while_the_plan_is_draft(item: Path, capsys) -> None:
    (item / "plan.md").write_text(_plan("draft"))
    assert cmd_gate("write", "src/sadana/x.py") == 2
    err = capsys.readouterr().err
    assert "plan.md is not approved" in err
    assert "the user writes 'approved' after reading it" in err


def test_source_write_is_refused_when_the_plan_does_not_name_the_path(item: Path, capsys) -> None:
    (item / "plan.md").write_text(_plan("approved", "src/sadana/other.py"))
    assert cmd_gate("write", "src/sadana/x.py") == 2
    err = capsys.readouterr().err
    assert "does not name src/sadana/x.py under '## Files that change'" in err
    assert "Add it there and tell the user you did — the approval was for the old list. Or stop." in err


def test_source_write_is_allowed_when_the_plan_names_the_path(item: Path) -> None:
    (item / "plan.md").write_text(_plan("approved", "src/sadana/x.py (new), tests/unit/test_x.py"))
    assert cmd_gate("write", "src/sadana/x.py") == 0
    assert cmd_gate("edit", "tests/unit/test_x.py") == 0


def test_source_write_is_allowed_by_a_glob_the_plan_names(item: Path) -> None:
    (item / "plan.md").write_text(_plan("approved", "src/sadana/console/*"))
    assert cmd_gate("write", "src/sadana/console/door.py") == 0


@pytest.mark.parametrize(
    "target",
    [
        "./src/sadana/x.py",
        "/REPO/src/sadana/x.py",
        "src/../etc/passwd",
    ],
    ids=["dot-slash", "absolute", "traversal"],
)
def test_an_oddly_spelled_guarded_path_is_still_refused(item: Path, target: str, tmp_path: Path) -> None:
    """Every shape that used to miss `^(src|tests)/` fell through to `return 0`.

    Allowed, not refused — the wrong direction for an unrecognised path. The
    absolute case is not hypothetical: the hook strips `$PWD/` from the path it
    is handed, which leaves an absolute one whenever `$PWD` and `getcwd()`
    differ by a symlink, and this repository has worktree symlinks.
    """
    (item / "plan.md").write_text(_plan("approved", "src/sadana/other.py"))
    assert cmd_gate("write", target.replace("/REPO", str(tmp_path))) == 2


@pytest.mark.parametrize("artifact", ["spec.md", "plan.md"])
def test_an_artifact_without_an_author_line_does_not_validate(tmp_path: Path, artifact: str) -> None:
    """The header rule is the load-bearing half of recording approval at all."""
    build = {"spec.md": _spec, "plan.md": _plan}[artifact]
    p = tmp_path / artifact
    p.write_text(build("draft").replace("Author: A (role). Status: draft.\n\n", ""))
    assert any("Author" in problem for problem in validate(p, BY_ARTIFACT[artifact]))


def test_a_path_outside_the_guarded_set_is_unaffected(item: Path) -> None:
    (item / "plan.md").write_text(_plan("draft", "src/sadana/other.py"))
    assert cmd_gate("write", "scripts/whatever.py") == 0
    # Not docs/reference/: check_reference_citations.py scans this file and
    # would read a path there as a real citation to a file that cannot exist.
    assert cmd_gate("write", "docs/notes/whatever.md") == 0


# ── the commit branch: all three ───────────────────────────────────────────
def _all_three(d: Path, intent: str, spec: str, plan: str) -> None:
    (d / "intent.md").write_text(_intent(intent))
    (d / "spec.md").write_text(_spec(spec))
    (d / "plan.md").write_text(_plan(plan))


def test_commit_is_refused_while_any_of_the_three_is_draft(item: Path, capsys) -> None:
    _all_three(item, "approved", "draft", "approved")
    assert cmd_gate("commit", "-") == 2
    assert "cannot commit" in capsys.readouterr().err


def test_commit_is_refused_when_the_build_stage_is_unapproved(item: Path, capsys) -> None:
    _all_three(item, "approved", "approved", "draft")
    assert cmd_gate("commit", "-") == 2
    err = capsys.readouterr().err
    assert "plan.md is not approved" in err


def test_commit_is_allowed_when_all_three_are_approved(item: Path) -> None:
    _all_three(item, "approved", "approved", "approved")
    assert cmd_gate("commit", "-") == 0


def test_status_names_the_missing_approval_while_a_stage_is_still_unwritten(item: Path, capsys) -> None:
    """The ordinary mid-chain arrangement, and the one the first fix missed.

    `waiting` was printed only when nothing else was outstanding, so with
    review.md not yet written the line never appeared — and `next: deploy`
    told the agent to write a file the gate was about to refuse.
    """
    _all_three(item, "draft", "draft", "draft")
    assert cmd_status(None) == 0
    out = capsys.readouterr().out
    assert "waiting on the user" in out
    assert "next: deploy" in out
    assert cmd_gate("write", rel(item, "review.md")) == 2


def test_status_does_not_call_an_unapproved_chain_ready_to_commit(item: Path, capsys) -> None:
    """`status` is the command that is supposed to say what is missing.

    Before approval existed it could only see validity, so a chain of four
    valid-but-unapproved artifacts printed "Ready to commit." and was then
    refused by the commit gate three lines later.
    """
    _all_three(item, "draft", "draft", "draft")
    (item / "review.md").write_text(_review())
    assert cmd_status(None) == 0
    out = capsys.readouterr().out
    assert "Ready to commit" not in out
    assert out.count("valid, awaiting approval") == 3  # not review.md: nothing reads its status
    assert "waiting on the user" in out
    assert cmd_gate("commit", "-") == 2
