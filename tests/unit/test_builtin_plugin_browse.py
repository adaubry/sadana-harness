"""The browse plugin, exercised as a plugin.

`execution.run_program` is stubbed throughout — the unit suite never launches a
browser. The real thing is `scripts/prove_browse.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sadana import execution
from sadana.builtin_seed import SOURCE_ROOT
from sadana.plugin_manifest import run_graph, validate
from sadana.plugins import Valid

_KEY_VAR = "SADANA_PLUGIN__BROWSE__API_KEY"


def _plugin_dir() -> Path:
    return SOURCE_ROOT / "browse"


async def _approve_ok(_plugin: str, _node: str, _value: object) -> bool:
    return True


async def _ask_unused(_skill: object, _text: str) -> str:
    raise AssertionError("this plugin has no ask node")


def _run(arguments: dict) -> object:
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
        )
    )


def _ran(stdout: str = "found it", exit_code: int = 0, stderr: str = "") -> execution.Ran:
    return execution.Ran(exit_code=exit_code, stdout=stdout, stderr=stderr, truncated=False)


@pytest.mark.unit
def test_the_shipped_plugin_validates() -> None:
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    assert outcome.manifest.name == "browse"
    assert outcome.manifest.entries[0].tool == "web.browse"
    assert outcome.manifest.settings[0].secret is True


@pytest.mark.unit
def test_the_entry_warns_that_pages_are_sent_to_a_model() -> None:
    """The second exposure, and the one a person approving "browse for me" has
    not obviously understood. It lives in the entry's own purpose so the agent
    repeats it."""
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    assert "sent to a model" in outcome.manifest.entries[0].purpose


@pytest.mark.unit
def test_a_browse_reports_what_the_browser_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_program", lambda _r: _ran("The plan costs 42 euros."))

    result = _run({"task": "find the price"})

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "The plan costs 42 euros." in result.text  # type: ignore[attr-defined]
    assert "not instructions" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_task_travels_as_its_own_argument_and_the_key_is_never_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_program` builds the child's environment from an allowlist, so the
    key has to be handed over deliberately — and the task has to be an
    argument, never part of the script."""
    seen: list[execution.ProgramRequest] = []

    def fake(request: execution.ProgramRequest) -> execution.ProgramOutcome:
        seen.append(request)
        return _ran()

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_program", fake)

    _run({"task": 'go to example.com; say "hi"'})

    request = seen[0]
    assert request.argv[-1] == 'go to example.com; say "hi"'
    assert request.env["OPENROUTER_API_KEY"] == "secret-token"  # pragma: allowlist secret
    assert "BROWSE_MODEL" in request.env
    # Nothing of this project's beyond the key, and telemetry off.
    assert set(request.env) == {"OPENROUTER_API_KEY", "BROWSE_MODEL", "ANONYMIZED_TELEMETRY"}


@pytest.mark.unit
def test_browsing_asks_for_longer_than_the_program_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[execution.ProgramRequest] = []

    def fake(request: execution.ProgramRequest) -> execution.ProgramOutcome:
        seen.append(request)
        return _ran()

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_program", fake)

    _run({"task": "x"})

    assert seen[0].timeout_s is not None
    assert seen[0].timeout_s > 120  # longer than execution.py own default


@pytest.mark.unit
def test_a_browser_that_will_not_run_is_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(
        execution, "run_program", lambda _r: execution.Failure(detail="could not be run: uvx not found")
    )

    result = _run({"task": "x"})

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "could not be run" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_task_the_browser_could_not_finish_says_what_went_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_program", lambda _r: _ran(exit_code=1, stderr="no browser found"))

    result = _run({"task": "x"})

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "could not finish" in result.text  # type: ignore[attr-defined]
    assert "no browser found" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_an_empty_task_never_starts_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret

    def explode(_request: object) -> object:
        raise AssertionError("an empty task must not start a browser")

    monkeypatch.setattr(execution, "run_program", explode)

    assert "No task" in _run({"task": "   "}).text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_run_with_no_key_never_reaches_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_KEY_VAR, raising=False)

    def explode(_request: object) -> object:
        raise AssertionError("no browser may start without a key")

    monkeypatch.setattr(execution, "run_program", explode)
    result = _run({"task": "x"})

    assert result.failed_node == "entry"  # type: ignore[attr-defined]
    assert "api_key" in result.text  # type: ignore[attr-defined]
