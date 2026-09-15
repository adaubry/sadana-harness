"""Tests for sadana.door.config_writer — the write half of config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from sadana.door import config_writer


@pytest.mark.unit
def test_apply_creates_a_table_for_a_two_segment_key() -> None:
    updated = config_writer.apply({}, "model_access.timeout_s", 45)
    assert updated == {"model_access": {"timeout_s": 45}}


@pytest.mark.unit
def test_apply_creates_a_subtable_for_a_three_segment_key() -> None:
    updated = config_writer.apply({}, "providers.openrouter.base_url", "https://example.test")
    assert updated == {"providers": {"openrouter": {"base_url": "https://example.test"}}}


@pytest.mark.unit
def test_apply_never_mutates_the_original() -> None:
    current = {"model_access": {"timeout_s": 30}}
    updated = config_writer.apply(current, "model_access.timeout_s", 45)
    assert current == {"model_access": {"timeout_s": 30}}
    assert updated == {"model_access": {"timeout_s": 45}}


@pytest.mark.unit
def test_apply_preserves_sibling_keys_in_the_same_table() -> None:
    current = {"model_access": {"provider": "openrouter", "timeout_s": 30}}
    updated = config_writer.apply(current, "model_access.timeout_s", 45)
    assert updated == {"model_access": {"provider": "openrouter", "timeout_s": 45}}


@pytest.mark.unit
def test_apply_refuses_a_key_with_no_table() -> None:
    with pytest.raises(ValueError):
        config_writer.apply({}, "bare_key", "value")


@pytest.mark.unit
@pytest.mark.parametrize("value", [[1, 2], {"a": 1}, None, object()])
def test_apply_refuses_an_unsupported_value_type(value: object) -> None:
    with pytest.raises(ValueError):
        config_writer.apply({}, "model_access.timeout_s", value)


@pytest.mark.unit
def test_write_round_trips_every_supported_scalar_type(tmp_path: Path) -> None:
    data = {
        "model_access": {
            "provider": "openrouter",
            "timeout_s": 30,
            "budget_fraction": 0.5,
            "enabled": True,
            "disabled": False,
        }
    }
    path = tmp_path / "config.toml"
    config_writer.write(path, data)
    assert tomllib.loads(path.read_text(encoding="utf-8")) == data


@pytest.mark.unit
def test_write_round_trips_one_level_of_subtables(tmp_path: Path) -> None:
    data = {"providers": {"openrouter": {"base_url": "https://example.test", "timeout_s": 10}}}
    path = tmp_path / "config.toml"
    config_writer.write(path, data)
    assert tomllib.loads(path.read_text(encoding="utf-8")) == data


@pytest.mark.unit
def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "config.toml"
    config_writer.write(path, {"model_access": {"timeout_s": 5}})
    assert path.exists()


@pytest.mark.unit
def test_write_refuses_a_string_toml_cannot_carry_and_touches_nothing(tmp_path: Path) -> None:
    """A lone surrogate — `plugins.toml_string`'s own documented refusal,
    reused here rather than re-hardened a second time."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError):
        config_writer.write(path, {"model_access": {"provider": "\ud800"}})
    assert not path.exists()


@pytest.mark.unit
def test_write_refuses_nesting_deeper_than_one_subtable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = {"a": {"b": {"c": {"d": 1}}}}
    with pytest.raises(ValueError):
        config_writer.write(path, data)
    assert not path.exists()


@pytest.mark.unit
def test_write_refuses_a_readback_mismatch_and_touches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The round-trip guard itself: an emitter that lies about what it wrote
    must be caught before the file is ever replaced."""
    monkeypatch.setattr(config_writer, "_emit", lambda data, **_: 'model_access = "not even a table"')
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError):
        config_writer.write(path, {"model_access": {"timeout_s": 30}})
    assert not path.exists()


@pytest.mark.unit
def test_write_is_atomic_no_temp_file_left_behind(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    directory.mkdir()
    path = directory / "config.toml"
    config_writer.write(path, {"model_access": {"timeout_s": 30}})
    assert sorted(p.name for p in directory.iterdir()) == ["config.toml"]
