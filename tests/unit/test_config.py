"""Tests for sadana.config: the resolution primitives and the two global paths."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sadana.config import Paths, env, env_bool, env_int, env_path, get_paths


@pytest.mark.unit
def test_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_TEST_STR", raising=False)
    assert env("SADANA_TEST_STR", "fallback") == "fallback"


@pytest.mark.unit
def test_env_returns_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_TEST_STR", "value")
    assert env("SADANA_TEST_STR", "fallback") == "value"


@pytest.mark.unit
def test_env_bool_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_TEST_BOOL", raising=False)
    assert env_bool("SADANA_TEST_BOOL", True) is True
    assert env_bool("SADANA_TEST_BOOL", False) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("off", False)],
)
def test_env_bool_parses_recognized_tokens(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("SADANA_TEST_BOOL", raw)
    assert env_bool("SADANA_TEST_BOOL", not expected) is expected


@pytest.mark.unit
def test_env_bool_raises_on_unrecognized_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_TEST_BOOL", "maybe")
    with pytest.raises(ValueError):
        env_bool("SADANA_TEST_BOOL", True)


@pytest.mark.unit
def test_env_int_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_TEST_INT", raising=False)
    assert env_int("SADANA_TEST_INT", 7) == 7


@pytest.mark.unit
def test_env_int_parses_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_TEST_INT", "42")
    assert env_int("SADANA_TEST_INT", 7) == 42


@pytest.mark.unit
def test_env_int_raises_on_unparseable_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_TEST_INT", "notanumber")
    with pytest.raises(ValueError):
        env_int("SADANA_TEST_INT", 7)


@pytest.mark.unit
def test_env_path_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_TEST_PATH", raising=False)
    default = Path("/fallback")
    assert env_path("SADANA_TEST_PATH", default) == default


@pytest.mark.unit
def test_env_path_returns_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_TEST_PATH", "/explicit/path")
    assert env_path("SADANA_TEST_PATH", Path("/fallback")) == Path("/explicit/path")


@pytest.mark.unit
def test_paths_is_frozen() -> None:
    paths = Paths(state_dir=Path("/a"), config_dir=Path("/b"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.state_dir = Path("/c")  # type: ignore[misc]


@pytest.mark.unit
def test_get_paths_honors_explicit_state_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "explicit-state"
    monkeypatch.setenv("SADANA_STATE_DIR", str(override))
    assert get_paths().state_dir == override


@pytest.mark.unit
def test_get_paths_falls_back_to_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SADANA_STATE_DIR", raising=False)
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    assert get_paths().state_dir == xdg / "sadana"


@pytest.mark.unit
def test_get_paths_falls_back_to_home_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert get_paths().state_dir == Path.home() / ".local" / "state" / "sadana"


@pytest.mark.unit
def test_get_paths_config_dir_honors_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert get_paths().config_dir == xdg / "sadana"


@pytest.mark.unit
def test_get_paths_config_dir_falls_back_to_home_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert get_paths().config_dir == Path.home() / ".config" / "sadana"


@pytest.mark.unit
def test_get_paths_does_not_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("SADANA_STATE_DIR", str(first))
    assert get_paths().state_dir == first
    monkeypatch.setenv("SADANA_STATE_DIR", str(second))
    assert get_paths().state_dir == second
