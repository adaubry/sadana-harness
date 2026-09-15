"""Tests for sadana.config: the resolution primitives and the two global paths."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sadana import config as config_module
from sadana.config import (
    Paths,
    bind_secret_reader,
    env,
    env_bool,
    env_int,
    env_path,
    get,
    get_paths,
    load_dotenv,
    secret,
)


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


# ── load_dotenv ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_dotenv_missing_file_is_a_no_op(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SADANA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("SADANA_TEST_LOADED", raising=False)
    load_dotenv()
    assert env("SADANA_TEST_LOADED", "fallback") == "fallback"


@pytest.mark.unit
def test_load_dotenv_no_longer_populates_os_environ(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """H14: `load_dotenv` stopped copying `.env` into `os.environ` —
    `config.secret` is the read path for these values now, and nothing
    downstream reads `os.environ` expecting `.env`'s contents to already
    be there."""
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text('AAA="quoted value"\nBBB=bare\n', encoding="utf-8")
    monkeypatch.setenv("SADANA_STATE_DIR", str(state))
    monkeypatch.delenv("AAA", raising=False)
    monkeypatch.delenv("BBB", raising=False)
    load_dotenv()
    assert env("AAA", "unset") == "unset"
    assert env("BBB", "unset") == "unset"


@pytest.mark.unit
def test_load_dotenv_never_overrides_a_live_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text('AAA="from-file"\n', encoding="utf-8")
    monkeypatch.setenv("SADANA_STATE_DIR", str(state))
    monkeypatch.setenv("AAA", "from-shell")
    load_dotenv()
    assert env("AAA", "") == "from-shell"


@pytest.mark.unit
def test_load_dotenv_raises_on_an_unreadable_encoding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A malformed `.env` still fails loudly at startup, the same moment it
    always has — deferring that failure to the first `config.secret()` call
    would turn a file problem into a much harder to diagnose runtime one."""
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_bytes(b"\xff\xfe\x00\x00not-utf8")
    monkeypatch.setenv("SADANA_STATE_DIR", str(state))
    with pytest.raises(UnicodeDecodeError):
        load_dotenv()


# ── get ───────────────────────────────────────────────────────────────────


def _write_config_toml(config_dir: Path, text: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(text, encoding="utf-8")


@pytest.mark.unit
def test_get_returns_default_when_file_and_key_are_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SADANA_MODEL_ACCESS_TIMEOUT_S", raising=False)
    assert get("model_access.timeout_s", 30) == 30


@pytest.mark.unit
def test_get_returns_the_files_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "sadana"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SADANA_MODEL_ACCESS_TIMEOUT_S", raising=False)
    _write_config_toml(config_dir, "[model_access]\ntimeout_s = 45\n")
    assert get("model_access.timeout_s", 30) == 45


@pytest.mark.unit
def test_get_environment_wins_over_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "sadana"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _write_config_toml(config_dir, "[model_access]\ntimeout_s = 45\n")
    monkeypatch.setenv("SADANA_MODEL_ACCESS_TIMEOUT_S", "99")
    assert get("model_access.timeout_s", 30) == 99


@pytest.mark.unit
def test_get_sees_a_rewritten_file_on_the_next_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Values of different lengths, not just different mtimes: the cache key
    # is (mtime_ns, size), and two writes close enough together could share
    # an mtime tick on some filesystems — a size difference makes this
    # assertion hold regardless of clock granularity.
    config_dir = tmp_path / "config" / "sadana"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SADANA_MODEL_ACCESS_TIMEOUT_S", raising=False)
    _write_config_toml(config_dir, "[model_access]\ntimeout_s = 45\n")
    assert get("model_access.timeout_s", 30) == 45
    _write_config_toml(config_dir, "[model_access]\ntimeout_s = 999999\n")
    assert get("model_access.timeout_s", 30) == 999999


@pytest.mark.unit
def test_get_reads_a_nested_dotted_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "sadana"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _write_config_toml(config_dir, '[providers.openrouter]\nbase_url = "https://example.test"\n')
    assert get("providers.openrouter.base_url", "default") == "https://example.test"


@pytest.mark.unit
def test_get_string_default_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SADANA_MODEL_ACCESS_PROVIDER", "anthropic")
    assert get("model_access.provider", "openrouter") == "anthropic"


@pytest.mark.unit
def test_get_bool_default_coerces_env_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SADANA_SOME_FLAG", "true")
    assert get("some.flag", False) is True


# ── secret ────────────────────────────────────────────────────────────────
#
# `secret()`'s reader fallback is exercised via `monkeypatch.setattr` on the
# module's own `_secret_reader`, the same idiom `test_model_access.py` uses
# for its `_REGISTRY`/`_discovered` globals — monkeypatch reverts it after
# each test with no reset fixture needed. `bind_secret_reader` itself gets
# one dedicated test below, with an explicit manual restore, since it is
# the one thing here monkeypatch did not set.


@pytest.mark.unit
def test_secret_returns_a_real_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-shell")
    monkeypatch.setattr(
        config_module, "_secret_reader", lambda name: pytest.fail("must not touch the file when env answers")
    )
    assert secret("OPENROUTER_API_KEY") == "sk-from-shell"


@pytest.mark.unit
def test_secret_falls_back_to_the_bound_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        config_module, "_secret_reader", lambda name: "from-file" if name == "OPENROUTER_API_KEY" else None
    )
    assert secret("OPENROUTER_API_KEY") == "from-file"


@pytest.mark.unit
def test_secret_sees_a_rewritten_reader_value_on_the_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A_SECRET", raising=False)  # pragma: allowlist secret
    values = {"A_SECRET": "old"}  # pragma: allowlist secret
    monkeypatch.setattr(config_module, "_secret_reader", lambda name: values.get(name))
    assert secret("A_SECRET") == "old"  # pragma: allowlist secret
    values["A_SECRET"] = "new"  # pragma: allowlist secret
    assert secret("A_SECRET") == "new"  # pragma: allowlist secret


@pytest.mark.unit
def test_secret_returns_none_when_the_reader_has_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    monkeypatch.setattr(config_module, "_secret_reader", lambda name: None)
    assert secret("MISSING_SECRET") is None


@pytest.mark.unit
def test_secret_raises_before_any_reader_is_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEVER_BOUND", raising=False)
    monkeypatch.setattr(config_module, "_secret_reader", None)
    with pytest.raises(RuntimeError):
        secret("NEVER_BOUND")


@pytest.mark.unit
def test_bind_secret_reader_sets_the_module_level_reader() -> None:
    original = config_module._secret_reader
    try:
        bind_secret_reader(lambda name: f"bound:{name}")
        assert config_module._secret_reader is not None
        assert config_module._secret_reader("X") == "bound:X"
    finally:
        config_module._secret_reader = original
