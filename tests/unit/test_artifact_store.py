"""Tests for sadana.artifact_store."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana import config
from sadana.artifact_store import (
    NoOutputDirectory,
    activate,
    contains,
    for_run,
    output_dir,
    run_dir,
    run_dir_name,
)

# ── run_dir_name ─────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("key", ["a/b", "../escape", "-leading", "with space", "UPPER", "..", "/"])
def test_run_dir_name_is_always_one_safe_path_component(key: str) -> None:
    """`sadana chat --key NAME` takes an arbitrary string, so every one of
    these is a conversation somebody can already have."""
    name = run_dir_name(key)
    assert name
    assert "/" not in name
    assert name not in (".", "..")
    assert not name.startswith("-")
    assert Path("/root", name).parent == Path("/root")


@pytest.mark.unit
def test_two_keys_the_encoding_flattens_still_get_different_directories() -> None:
    """The whole reason a digest is appended: without it these two
    conversations would write into one directory."""
    assert run_dir_name("a/b") != run_dir_name("a-b")


@pytest.mark.unit
def test_run_dir_name_is_stable_for_the_same_key() -> None:
    assert run_dir_name("debugging-session") == run_dir_name("debugging-session")


@pytest.mark.unit
def test_run_dir_name_keeps_the_key_readable() -> None:
    """A person reads this path out of a plugin's own answer."""
    assert run_dir_name("debugging-session").startswith("debugging-session-")


# ── run_dir / for_run ────────────────────────────────────────────────────


@pytest.mark.unit
def test_run_dir_is_under_the_state_directory_and_creates_nothing(tmp_path: Path) -> None:
    directory = run_dir(tmp_path, "k", 3, 1)
    assert tmp_path in directory.parents
    assert not directory.exists()
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.unit
def test_run_dir_separates_every_part_of_the_runs_own_key(tmp_path: Path) -> None:
    """The key `plugin_runs` is already keyed by, so the files and the record
    of the run line up by reading either one."""
    base = run_dir(tmp_path, "k", 3, 1)
    assert base != run_dir(tmp_path, "k", 3, 2)
    assert base != run_dir(tmp_path, "k", 4, 1)
    assert base != run_dir(tmp_path, "other", 3, 1)


@pytest.mark.unit
def test_for_run_follows_the_state_directory_rather_than_capturing_it() -> None:
    """Resolved fresh per call, never at import — an import-time capture goes
    stale under the fixture that redirects the state directory."""
    assert config.get_paths().state_dir in for_run("k", 0, 0).parents


# ── activate / output_dir ────────────────────────────────────────────────


@pytest.mark.unit
def test_output_dir_raises_when_no_run_is_current() -> None:
    with pytest.raises(NoOutputDirectory):
        output_dir()


@pytest.mark.unit
def test_output_dir_raises_inside_a_run_that_was_given_none() -> None:
    with activate(None), pytest.raises(NoOutputDirectory):
        output_dir()


@pytest.mark.unit
def test_output_dir_creates_the_directory_on_the_first_ask(tmp_path: Path) -> None:
    wanted = tmp_path / "artifacts" / "k" / "0" / "0"
    with activate(wanted):
        assert not wanted.exists()
        assert output_dir() == wanted
        assert wanted.is_dir()


@pytest.mark.unit
def test_output_dir_is_idempotent(tmp_path: Path) -> None:
    wanted = tmp_path / "out"
    with activate(wanted):
        output_dir()
        (wanted / "already.txt").write_text("kept")
        assert output_dir() == wanted
        assert (wanted / "already.txt").read_text() == "kept"


@pytest.mark.unit
def test_activate_restores_what_it_replaced(tmp_path: Path) -> None:
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    with activate(outer):
        with activate(inner):
            assert output_dir() == inner
        assert output_dir() == outer
    with pytest.raises(NoOutputDirectory):
        output_dir()


@pytest.mark.unit
def test_activate_restores_even_when_the_body_raises(tmp_path: Path) -> None:
    """A leaked activation does not fail later — it makes `output_dir()`
    succeed in a run that should have had none, and something writes into a
    stale directory with nothing to notice."""
    with activate(tmp_path / "outer"):
        with pytest.raises(ValueError), activate(tmp_path / "inner"):
            raise ValueError("boom")
        assert output_dir() == tmp_path / "outer"


# ── contains ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_contains_accepts_a_file_inside_the_directory(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    assert contains(directory, str(directory / "chart.png"))
    assert contains(directory, str(directory / "nested" / "chart.png"))


@pytest.mark.unit
@pytest.mark.parametrize("escape", ["/etc/passwd", "sibling/chart.png", "run/../sibling/chart.png"])
def test_contains_refuses_anything_outside_it(tmp_path: Path, escape: str) -> None:
    directory = tmp_path / "run"
    directory.mkdir()
    (tmp_path / "sibling").mkdir()
    candidate = escape if escape.startswith("/") else str(tmp_path / escape)
    assert not contains(directory, candidate)


@pytest.mark.unit
def test_contains_refuses_a_symlink_inside_pointing_out(tmp_path: Path) -> None:
    """The half the name pattern cannot make: the path is spelled correctly
    and still lands somewhere else."""
    directory = tmp_path / "run"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "escape").symlink_to(outside)

    assert not contains(directory, str(directory / "escape" / "stolen.txt"))


@pytest.mark.unit
def test_contains_refuses_the_directory_itself(tmp_path: Path) -> None:
    """A directory is not a file in itself; recording one as a file artifact
    would describe a folder as something a person can open."""
    directory = tmp_path / "run"
    directory.mkdir()
    assert not contains(directory, str(directory))


@pytest.mark.unit
def test_contains_answers_rather_than_raising_on_a_string_pathlib_rejects() -> None:
    """`Path("a\0b").resolve()` raises ValueError, not OSError — a predicate
    returning bool must not raise on input it is asked to judge."""
    assert not contains(Path("/tmp"), "a\0b")
