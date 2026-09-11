"""Tests for sadana.execution: run_http()'s never-raises contract.

The network boundary (``execution._open``) is monkeypatched, same
point ``tests/unit/test_model_providers_openrouter.py`` patches for the
identical reason — it's the one canonical shared module regardless of
import path.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sadana import execution
from sadana.execution import Failure, HttpRequest, ProgramRequest, Ran, Success, run_http, run_program


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        """Takes `amt` because the real one does, and `run_http` now passes a
        ceiling rather than reading whatever the other end decides to send."""
        return self._body if amt is None else self._body[:amt]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.mark.unit
def test_2xx_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution, "_open", lambda req, timeout_s=None: _FakeResponse(200, b"ok"))
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert outcome == Success(status=200, body=b"ok")


@pytest.mark.unit
def test_http_error_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise urllib.error.HTTPError(
            "https://example.com",
            404,
            "not found",
            hdrs=None,
            fp=io.BytesIO(b""),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)
    assert "404" in outcome.detail


@pytest.mark.unit
def test_http_error_detail_reads_the_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise urllib.error.HTTPError(
            "https://example.com",
            429,
            "too many requests",
            hdrs=None,
            fp=io.BytesIO(b"quota exceeded, retry after 30s"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)
    assert "quota exceeded, retry after 30s" in outcome.detail


@pytest.mark.unit
def test_unsupported_scheme_is_failure_without_ever_calling_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def _tripwire(req: object, timeout: float | None = None) -> None:
        calls.append(req)

    monkeypatch.setattr(execution, "_open", _tripwire)
    outcome = run_http(HttpRequest(method="GET", url="file:///etc/passwd"))
    assert isinstance(outcome, Failure)
    assert calls == []


@pytest.mark.unit
def test_malformed_url_from_urlopen_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise ValueError("Invalid header value b'bar\\r\\nX-Injected: evil'")

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)


@pytest.mark.unit
def test_connection_failure_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://does-not-resolve.invalid"))
    assert isinstance(outcome, Failure)
    assert "Name or service not known" in outcome.detail


@pytest.mark.unit
def test_a_redirect_is_refused_rather_than_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential sent as a header would otherwise be re-sent to whatever
    the 3xx points at: urllib's default redirect handler keeps every header
    but content-length and content-type, cross-host included.

    Asserted against the handler this module installs rather than over a
    socket, so it stays inside testing-conventions' network ban."""
    assert execution._NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid") is None


@pytest.mark.unit
@pytest.mark.parametrize(("asked", "used"), [(None, 7), (120, 120)])
def test_a_request_may_ask_for_longer_than_the_configured_default(
    monkeypatch: pytest.MonkeyPatch, asked: int | None, used: int
) -> None:
    """Drawing a picture takes tens of seconds; raising the default instead
    would make a search that should fail fast wait just as long."""
    seen: list[int] = []

    def _capture(_req: object, timeout_s: int) -> object:
        seen.append(timeout_s)
        return _FakeResponse(200, b"ok")

    monkeypatch.setenv("SADANA_EXECUTION_HTTP_TIMEOUT_S", "7")
    monkeypatch.setattr(execution, "_open", _capture)
    run_http(HttpRequest(method="GET", url="https://example.com", timeout_s=asked))
    assert seen == [used]


@pytest.mark.unit
def test_an_http_error_body_is_defanged_before_it_becomes_a_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """`detail` is built from up to 200 bytes of a stranger's response body and
    is read by a model. Defanged here rather than in each plugin body that
    reports it — every caller having to remember is how the third one silently
    forgets."""

    def _raise(_req: object, timeout_s: int = 0) -> object:
        raise urllib.error.HTTPError(
            "https://example.com",
            500,
            "Server Error",
            {},  # type: ignore[arg-type]
            io.BytesIO("oh \x1b[31mno\u200b\u202e".encode()),
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "\x1b" not in outcome.detail
    assert "\u200b" not in outcome.detail
    assert "\u202e" not in outcome.detail
    assert "oh" in outcome.detail


@pytest.mark.unit
def test_a_reply_larger_than_the_ceiling_is_refused_rather_than_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing else bounds a reply: without a ceiling its size is whatever the
    other end decides to send, and the first caller that both expects
    megabytes and commits them to disk also asks for a 180-second window to
    receive them."""
    oversized = b"x" * (execution.MAX_BODY_BYTES + 1)
    monkeypatch.setattr(execution, "_open", lambda _req, timeout_s=0: _FakeResponse(200, oversized))

    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "larger than" in outcome.detail


@pytest.mark.unit
def test_a_reply_exactly_at_the_ceiling_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    at_limit = b"x" * execution.MAX_BODY_BYTES
    monkeypatch.setattr(execution, "_open", lambda _req, timeout_s=0: _FakeResponse(200, at_limit))

    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Success)
    assert len(outcome.body) == execution.MAX_BODY_BYTES


@pytest.mark.unit
def test_the_status_reason_is_defanged_too_not_only_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason phrase is as much the other end's as the body is —
    `http.client` reads it off the status line. Defanging only the body was
    this filter's first placement, and it was a narrowing."""

    def _raise(_req: object, timeout_s: int = 0) -> object:
        raise urllib.error.HTTPError(
            "https://example.com",
            500,
            "oops \x1b[31mEVIL",
            {},  # type: ignore[arg-type]
            io.BytesIO(b""),
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "\x1b" not in outcome.detail
    assert "oops" in outcome.detail


# ── run_program ──────────────────────────────────────────────────────────
#
# These run real processes — `sys.executable -c` only, which is present by
# construction, deterministic, and not the network. A test that shelled out to
# `echo` or `sleep` would be testing the developer's PATH.


def _python(source: str, **kwargs: object) -> ProgramRequest:
    return ProgramRequest(argv=(sys.executable, "-c", source), **kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_program_that_succeeds_reports_what_it_printed() -> None:
    outcome = run_program(_python("import sys; print('hello'); print('bad', file=sys.stderr)"))
    assert isinstance(outcome, Ran)
    assert outcome.exit_code == 0
    assert outcome.stdout.strip() == "hello"
    assert outcome.stderr.strip() == "bad"
    assert outcome.truncated is False


@pytest.mark.unit
def test_a_non_zero_exit_is_a_result_not_a_failure() -> None:
    """The program ran and said no, which is a different thing from not
    running at all — and a caller has to be able to tell them apart."""
    outcome = run_program(_python("raise SystemExit(3)"))
    assert isinstance(outcome, Ran)
    assert outcome.exit_code == 3


@pytest.mark.unit
def test_no_secret_this_project_holds_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted the only way that means anything: the secrets are set in the
    *parent* and the child prints the environment it actually got. An
    assertion over an environment that never held one passes while the
    exposure is wide open."""
    monkeypatch.setenv("SADANA_PLUGIN__WEB_SEARCH__API_KEY", "brave-key")  # pragma: allowlist secret
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")  # pragma: allowlist secret
    monkeypatch.setenv("SOMETHING_ELSE", "inherited-by-accident")

    outcome = run_program(_python("import json, os; print(json.dumps(dict(os.environ)))"))

    assert isinstance(outcome, Ran)
    child = json.loads(outcome.stdout)
    assert "SADANA_PLUGIN__WEB_SEARCH__API_KEY" not in child
    assert "OPENROUTER_API_KEY" not in child
    assert "SOMETHING_ELSE" not in child
    # …and what a program needs to work is still there.
    assert "PATH" in child


@pytest.mark.unit
def test_what_the_caller_passes_reaches_the_child_and_beats_the_allowlist() -> None:
    """`HOME` is genuinely in the allowlist and genuinely set in the parent,
    so this proves both halves: the value arrived, and it won."""
    outcome = run_program(_python("import os; print(os.environ['HOME'])", env={"HOME": "/somewhere/else"}))
    assert isinstance(outcome, Ran)
    assert outcome.stdout.strip() == "/somewhere/else"


@pytest.mark.unit
def test_nothing_in_argv_is_ever_interpreted() -> None:
    """No shell anywhere in this path, so a semicolon is a semicolon."""
    outcome = run_program(ProgramRequest(argv=(sys.executable, "-c", "import sys; print(sys.argv[1])", "a; rm -rf /")))
    assert isinstance(outcome, Ran)
    assert outcome.stdout.strip() == "a; rm -rf /"


@pytest.mark.unit
def test_a_program_runs_where_it_is_told(tmp_path: Path) -> None:
    outcome = run_program(_python("import os; print(os.getcwd())", cwd=tmp_path))
    assert isinstance(outcome, Ran)
    assert Path(outcome.stdout.strip()).resolve() == tmp_path.resolve()


@pytest.mark.unit
def test_an_empty_argv_is_refused_before_anything_is_started() -> None:
    outcome = run_program(ProgramRequest(argv=()))
    assert isinstance(outcome, Failure)
    assert "no program" in outcome.detail


@pytest.mark.unit
def test_a_program_that_does_not_exist_says_so_rather_than_raising() -> None:
    outcome = run_program(ProgramRequest(argv=("definitely-not-a-real-program-xyzzy",)))
    assert isinstance(outcome, Failure)
    assert "could not be run" in outcome.detail


@pytest.mark.unit
def test_a_working_directory_that_does_not_exist_is_a_returned_failure(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    outcome = run_program(_python("print(1)", cwd=missing))
    assert isinstance(outcome, Failure)
    assert "could not be run" in outcome.detail
    assert "nope" in outcome.detail


@pytest.mark.unit
@pytest.mark.parametrize(("per_request", "from_config"), [(1, None), (None, "1")])
def test_a_program_that_outlives_its_limit_is_stopped(
    monkeypatch: pytest.MonkeyPatch, per_request: int | None, from_config: str | None
) -> None:
    """Both sources of the limit, in one test: the per-request override and
    the config key."""
    if from_config is not None:
        monkeypatch.setenv("SADANA_EXECUTION_PROGRAM_TIMEOUT_S", from_config)
    started = time.monotonic()

    outcome = run_program(_python("import time; time.sleep(30)", timeout_s=per_request))
    elapsed = time.monotonic() - started

    assert isinstance(outcome, Failure)
    assert "stopped" in outcome.detail
    assert "1s" in outcome.detail
    # Loose bound on purpose: the claim is "it did not wait 30 seconds", not a
    # timing assertion that fails on a busy machine.
    assert elapsed < 15


@pytest.mark.unit
def test_output_beyond_the_cap_is_cut_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_EXECUTION_PROGRAM_OUTPUT_BYTES", "100")
    outcome = run_program(_python("print('x' * 5000)"))
    assert isinstance(outcome, Ran)
    assert outcome.truncated is True
    assert len(outcome.stdout) == 100


@pytest.mark.unit
def test_output_exactly_at_the_cap_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boundary the read-one-past trick exists for, and the one most
    likely to be off by one."""
    monkeypatch.setenv("SADANA_EXECUTION_PROGRAM_OUTPUT_BYTES", "10")
    outcome = run_program(_python("import sys; sys.stdout.write('y' * 10)"))
    assert isinstance(outcome, Ran)
    assert outcome.truncated is False
    assert outcome.stdout == "y" * 10


@pytest.mark.unit
def test_the_time_limit_follows_its_config_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_EXECUTION_PROGRAM_TIMEOUT_S", "1")
    outcome = run_program(_python("import time; time.sleep(30)"))
    assert isinstance(outcome, Failure)
    assert "1s" in outcome.detail


@pytest.mark.unit
def test_the_child_gets_a_utf8_locale_rather_than_the_parents(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_read_capped` decodes as UTF-8, so inheriting the locale would let a
    parent carrying `LC_ALL=C` produce a child whose output this reader then
    mangles by replacement — the exact case the allowlist exists to prevent.
    `scripts/run_tests.sh` sets the same two names for its own children."""
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")

    outcome = run_program(_python("import os; print(os.environ['LC_ALL'], os.environ['LANG'])"))

    assert isinstance(outcome, Ran)
    assert outcome.stdout.split() == ["C.UTF-8", "C.UTF-8"]


@pytest.mark.unit
def test_a_child_is_not_told_it_is_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent on purpose: a child that cannot tell emits far fewer escape
    sequences for anything downstream to strip.

    `monkeypatch` is load-bearing — the hermetic runner uses `env -i` and
    passes no `TERM`, so without setting one here this passes even if `TERM`
    were added to the allowlist."""
    monkeypatch.setenv("TERM", "xterm-256color")
    outcome = run_program(_python("import os; print('TERM' in os.environ)"))
    assert isinstance(outcome, Ran)
    assert outcome.stdout.strip() == "False"


@pytest.mark.unit
def test_a_timeout_kills_what_the_program_spawned_too() -> None:
    """Without `start_new_session` the timeout kills one pid, its children are
    reparented, and they keep running — so "nothing runs forever" would have
    been false for any program that spawns, which is most of what this is for.

    The grandchild writes a file after a delay; if it survived the kill, the
    file appears while this test is still watching for it."""
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "grandchild-survived"
        outcome = run_program(
            _python(
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', "
                f"\"import time; time.sleep(2); open({str(marker)!r}, 'w').write('x')\"])\n"
                "time.sleep(30)",
                timeout_s=1,
            )
        )

        assert isinstance(outcome, Failure)
        assert "stopped" in outcome.detail
        time.sleep(3)
        assert not marker.exists(), "the spawned grandchild outlived the timeout"


@pytest.mark.unit
def test_a_program_that_writes_without_end_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Written output is not free just because it is discarded: with no
    ceiling a print loop put over a gigabyte into the temp directory in two
    seconds, which is the same class of accident the other two limits stop."""
    monkeypatch.setenv("SADANA_EXECUTION_PROGRAM_OUTPUT_BYTES", "1024")

    outcome = run_program(_python("import sys\nwhile True:\n    sys.stdout.write('x' * 65536)", timeout_s=30))

    assert isinstance(outcome, Failure)
    assert "wrote more than" in outcome.detail


@pytest.mark.unit
def test_what_the_allowlist_lets_through_really_does_reach_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PATH` is asserted elsewhere; `HOME` and `TZ` were not, so deleting
    either from the allowlist broke no test."""
    monkeypatch.setenv("HOME", "/a/particular/home")
    monkeypatch.setenv("TZ", "Europe/Paris")

    outcome = run_program(_python("import os; print(os.environ['HOME'], os.environ['TZ'])"))

    assert isinstance(outcome, Ran)
    assert outcome.stdout.split() == ["/a/particular/home", "Europe/Paris"]
