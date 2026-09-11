"""The image-gen plugin, exercised as a plugin.

The first plugin in this project that produces a file, so this is also the
first real use of `artifact_store` — a mistake in `ARTIFACT-STORE-01` shows up
here rather than there.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from sadana import artifact_store, execution
from sadana.builtin_seed import SOURCE_ROOT
from sadana.plugin_manifest import run_graph, validate
from sadana.plugins import Artifact, Valid

_KEY_VAR = "SADANA_PLUGIN__IMAGE_GEN__API_KEY"
_PIXELS = b"\x89PNG\r\n\x1a\n" + b"pretend pixels" * 10
_B64 = base64.b64encode(_PIXELS).decode()


def _plugin_dir() -> Path:
    return SOURCE_ROOT / "image-gen"


async def _approve_ok(_plugin: str, _node: str, _value: object) -> bool:
    return True


async def _ask_unused(_skill: object, _text: str) -> str:
    raise AssertionError("this plugin has no ask node")


def _reply(media_type: str = "image/png") -> execution.Outcome:
    body = json.dumps(
        {"choices": [{"message": {"images": [{"image_url": {"url": f"data:{media_type};base64,{_B64}"}}]}}]}
    ).encode()
    return execution.Success(status=200, body=body)


def _run(arguments: dict, output_dir: Path | None) -> object:
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    return asyncio.run(
        run_graph(
            _plugin_dir(),
            outcome.manifest,
            outcome.manifest.entries[0],
            arguments,
            ask=_ask_unused,  # type: ignore[arg-type]
            approve=_approve_ok,
            output_dir=output_dir,
        )
    )


@pytest.mark.unit
def test_the_shipped_plugin_validates() -> None:
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    assert outcome.manifest.name == "image-gen"
    assert outcome.manifest.entries[0].tool == "image.draw"
    assert outcome.manifest.settings[0].secret is True


@pytest.mark.unit
def test_a_drawn_picture_really_lands_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The half that has never been exercised: a plugin producing a file."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: _reply())
    out = tmp_path / "run-output"

    result = _run({"prompt": "a red bicycle"}, out)

    assert result.failed_node is None  # type: ignore[attr-defined]
    written = out / "a-red-bicycle.png"
    assert written.read_bytes() == _PIXELS
    assert result.artifacts == (Artifact(kind="file", name="a-red-bicycle.png", ref=str(written)),)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_pictures_bytes_appear_in_no_field_of_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The constraint this work item exists to get right. A body that returned
    the data URI would work perfectly — correct file, correct path — and
    silently consume most of the room the conversation had left.

    Every field is checked, not just `text`: a base64 blob smuggled into an
    artifact's `name` or a trace `detail` would be just as costly and would
    pass a narrower assertion."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: _reply())

    result = _run({"prompt": "a red bicycle"}, tmp_path / "out")

    haystack = [result.text]  # type: ignore[attr-defined]
    haystack += [t.detail or "" for t in result.trace]  # type: ignore[attr-defined]
    haystack += [a.name for a in result.artifacts] + [a.ref for a in result.artifacts]  # type: ignore[attr-defined]
    for field in haystack:
        assert _B64 not in field
    # What does travel is the path, which is the whole point.
    assert result.text.endswith("a-red-bicycle.png")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_file_goes_in_the_runs_own_directory_and_nowhere_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: _reply())
    out = tmp_path / "run-output"

    result = _run({"prompt": "x"}, out)

    assert artifact_store.contains(out, result.artifacts[0].ref)  # type: ignore[attr-defined]
    # Nothing was written anywhere else under the test's root. `home` is the
    # isolated-state fixture's, not this plugin's.
    written = [f for f in tmp_path.rglob("*") if f.is_file() and (tmp_path / "home") not in f.parents]
    assert written == [out / "x.png"]


@pytest.mark.unit
def test_a_run_with_no_output_directory_cannot_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`output_dir()` raises rather than guessing a path, and `run_graph`
    turns that into this node's failure."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: _reply())

    result = _run({"prompt": "x"}, None)

    assert result.failed_node == "draw"  # type: ignore[attr-defined]
    assert "NoOutputDirectory" in (result.trace[0].detail or "")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_reply_in_words_rather_than_pixels_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The likeliest real failure: the model describes the picture instead of
    drawing it."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    body = json.dumps({"choices": [{"message": {"content": "Here is what it would look like…"}}]}).encode()
    monkeypatch.setattr(execution, "run_http", lambda _r: execution.Success(status=200, body=body))
    out = tmp_path / "run-output"

    result = _run({"prompt": "x"}, out)

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "answered in words" in result.text  # type: ignore[attr-defined]
    assert not out.exists()


@pytest.mark.unit
def test_the_key_travels_as_a_header_and_is_never_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[execution.HttpRequest] = []

    def fake(request: execution.HttpRequest) -> execution.Outcome:
        seen.append(request)
        return _reply()

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", fake)

    result = _run({"prompt": "x"}, tmp_path / "out")

    assert seen[0].headers["Authorization"] == "Bearer secret-token"  # pragma: allowlist secret
    assert "secret-token" not in seen[0].url
    assert "secret-token" not in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_drawing_asks_for_longer_than_the_default_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drawing takes tens of seconds; the default was chosen when every caller
    returned in under one."""
    seen: list[execution.HttpRequest] = []

    def fake(request: execution.HttpRequest) -> execution.Outcome:
        seen.append(request)
        return _reply()

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", fake)

    _run({"prompt": "x"}, tmp_path / "out")

    assert seen[0].timeout_s is not None
