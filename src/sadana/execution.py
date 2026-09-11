"""EXECUTION — what a call step may do.

Two ways to reach outside this process, and nothing else: `run_http` makes one
outward HTTP request, `run_program` runs one local program. Both are consumed
by plugin bodies, both return rather than raise, and both share `Failure`.

Kept in one module deliberately: they share nothing but that contract, and the
alternative is two files that both mean "reaching outside" with no rule anyone
can state about which goes where (`docs/tasks/SUBPROCESS-01-running-a-program
-and-saying-what-that-costs/spec.md` § Design). **The trigger for revisiting is
a third kind of outward reach, not more lines.**

Neither is a sandbox, and the difference between them matters here. `run_http`
was specified when this project had no marketplace and no untrusted source —
that is no longer true (`PLUGIN-MARKET-01`, `PLUGIN-INSTALL-01`), and the
posture question it deferred is answered for `run_program` in that function's
own docstring rather than here.

`run_program` is the only way a *plugin* runs a program. It is not the only
`subprocess` call in this project — `plugin_install._git` and
`gateway_service._run_systemctl` predate it, and the first of those still
inherits the whole parent environment, which is the exposure `run_program`
exists to avoid. Converting it is owed as its own work item.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from sadana import config
from sadana.untrusted_text import defang


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect, which makes a 3xx an ``HTTPError`` and so a
    ``Failure`` through the handler below.

    Not caution for its own sake. ``urllib``'s default handler rebuilds the
    request for the new location keeping every header but content-length and
    content-type — so a credential sent as a header (which is how
    `PLUGIN-CONFIG-01` settings reach a service) is re-sent to whatever the
    3xx points at, cross-host included. A hijacked DNS record, an
    intercepting proxy, or an upstream change is then handed the key. This is
    the exfiltration shape `tools/browser_tool.py:4186-4198` guards from the
    other direction, and there is no legitimate redirect on this path today.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: ARG002
        return None


# How much of a `Failure.detail` a caller may hand onward. A model reads it.
_MAX_DETAIL_CHARS = 200

# What one reply may be. Nothing else here bounds it: `resp.read()` with no
# ceiling means a reply's size is whatever the other end decides to send, and
# the first caller that both expects megabytes and commits them to disk
# (`image-gen`) also asks for a 180-second window to receive them.
MAX_BODY_BYTES = 32 * 1024 * 1024


def _safe(detail: str) -> str:
    """One `Failure.detail`, filtered and bounded."""
    return defang(detail)[:_MAX_DETAIL_CHARS]


def _open(req: urllib.request.Request, timeout_s: int):  # type: ignore[no-untyped-def]
    """This module's one network boundary, named so it can be stubbed.

    A test that patches `urllib.request.urlopen` instead is patching a symbol
    this module may stop calling — which is exactly what happened when the
    redirect handler above was added: the stub kept matching a function
    nothing called, and the suite quietly made real requests. One named seam
    cannot fail that way.
    """
    return urllib.request.build_opener(_NoRedirects).open(req, timeout=timeout_s)


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    # How long this one call may take, when the config default is wrong for
    # it. `None` means the default, which is every caller that predates this
    # field. Drawing a picture takes tens of seconds where every other caller
    # here returns in under one, and raising the default instead would make a
    # search that should fail fast hang for as long as the slowest caller
    # needs (`docs/tasks/IMAGE-GEN-01-the-first-plugin-that-makes-a-file
    # /spec.md`). The default itself stays a config key — CLAUDE.md keeps
    # behaviours in config; this is an override, not a second source of truth.
    timeout_s: int | None = None


@dataclass(frozen=True)
class Success:
    status: int
    body: bytes


@dataclass(frozen=True)
class Failure:
    detail: str  # one safe line — never a stack trace


Outcome = Success | Failure


def run_http(request: HttpRequest) -> Outcome:
    """Make exactly one outward HTTP request. Never raises."""
    if not request.url.lower().startswith(("http://", "https://")):
        # Bounds what a call step can reach through this path: outward HTTP
        # only, never a local file or another scheme urllib also understands.
        return Failure(_safe(f"unsupported URL scheme: {request.url!r} (only http/https are allowed)"))
    timeout_s = (
        request.timeout_s if request.timeout_s is not None else config.env_int("SADANA_EXECUTION_HTTP_TIMEOUT_S", 30)
    )
    try:
        req = urllib.request.Request(request.url, data=request.body, headers=request.headers, method=request.method)
        with _open(req, timeout_s) as resp:
            body = resp.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                return Failure(f"the reply was larger than {MAX_BODY_BYTES} bytes and was not read")
            return Success(status=resp.status, body=body)
    except urllib.error.HTTPError as exc:
        # Every `Failure.detail` this function builds is a string a model
        # will read, and every one of them can carry third-party text — so
        # the filter goes on the composed value, not on one ingredient of it.
        # The reason phrase is as much theirs as the body: `http.client`
        # reads it off the status line and decodes it latin-1, so escapes and
        # C1 controls pass straight through. Defanging only the body was this
        # filter's first placement and it was a narrowing, not a centralising.
        detail = exc.read().decode(errors="replace").strip() or exc.reason
        return Failure(_safe(f"HTTP {exc.code}: {detail}"))
    except (OSError, ValueError) as exc:
        return Failure(_safe(str(exc)))


# ── running a program ────────────────────────────────────────────────────
# `docs/tasks/SUBPROCESS-01-running-a-program-and-saying-what-that-costs
# /spec.md`.

# What a child is allowed to inherit. An allowlist, not a denylist, and the
# distinction is the point: `PLUGIN-CONFIG-01` mints a new `SADANA_PLUGIN__*`
# secret every time somebody declares a setting, so a denylist would have to
# keep knowing what a secret looks like, forever, correctly. This knows
# nothing about secrets and so cannot fall behind them.
#
# `PATH` is the one real choice here rather than an obvious one — inheriting it
# means a program resolves the way the person's own shell would, which is both
# what they expect and how a shadowed binary wins. See spec.md § Open
# questions.
_INHERITED_ENV = ("PATH", "HOME", "TZ")

# Set rather than inherited, and for the same reason `scripts/run_tests.sh`
# sets them for its own children: `_read_capped` decodes as UTF-8, so a parent
# carrying `LC_ALL=C` would give a child whose output this reader then mangles
# by replacement — the exact "mangle text" case the allowlist exists to
# prevent. Overridable through `request.env`, which wins.
#
# `TERM` is absent on purpose and it is a quiet win: a child that cannot tell
# it is on a terminal emits far fewer escape sequences to be stripped later.
_FIXED_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


@dataclass(frozen=True)
class ProgramRequest:
    """One program to run. `argv` is a sequence from the start and there is no
    shell anywhere in this path, so nothing a model wrote can become part of a
    command that gets interpreted."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    # Merged over the allowlist, so a caller can pass a program the one
    # credential it needs without every other one coming along.
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_s: int | None = None


@dataclass(frozen=True)
class Ran:
    """A program that ran. A non-zero `exit_code` is this, not a `Failure` —
    the program ran and said no, which is a different thing from not running
    at all."""

    exit_code: int
    stdout: str
    stderr: str
    truncated: bool


ProgramOutcome = Ran | Failure


def _child_env(extra: Mapping[str, str]) -> dict[str, str]:
    built = {name: os.environ[name] for name in _INHERITED_ENV if name in os.environ}
    built.update(_FIXED_ENV)
    built.update(extra)
    return built


def _read_capped(handle: IO[bytes], cap: int) -> tuple[str, bool]:
    """At most `cap` bytes back, and whether there were more.

    Reads one byte past the cap, which is what distinguishes "exactly at the
    limit" from "truncated" without a second stat."""
    handle.seek(0)
    raw = handle.read(cap + 1)
    return raw[:cap].decode("utf-8", errors="replace"), len(raw) > cap


# How much either stream may be *written* before the program is stopped, as a
# multiple of what will be read back. Written output is not free just because
# it is discarded: with no ceiling a program in a print loop put 1.12 GB into
# the temp directory in two seconds, and at the default time limit that is tens
# of gigabytes written and thrown away — an accident exactly like the ones the
# other two limits exist to stop. Generous, because a program legitimately
# printing more than will be read is normal and gets truncated, not killed.
_WRITE_CEILING_FACTOR = 64

# How often the wait below looks up to check. Short enough that a runaway is
# caught in a fraction of a second, long enough to cost nothing.
_POLL_S = 0.1


def _kill_group(child: subprocess.Popen) -> None:
    """Kill the whole process group and reap, best effort. Whatever the child
    spawned is in that group because of `start_new_session`.

    **Only safe because of that flag**, and the two must never be separated:
    without it the child shares *this* process's group, and `killpg` takes
    down the caller. Removing `start_new_session` to see what the grandchild
    test would catch killed the entire test run, which is the loudest possible
    demonstration of why."""
    try:
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # already gone, or not ours
        child.kill()
    child.wait()


def _wait_bounded(
    child: subprocess.Popen,
    *,
    timeout_s: int,
    disk_ceiling: int,
    streams: tuple[IO[bytes], IO[bytes]],
) -> Failure | None:
    """Wait for the child, stopping it if it takes too long or writes too
    much. `None` means it finished on its own."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            child.wait(timeout=_POLL_S)
            return None
        except subprocess.TimeoutExpired:
            pass
        if any(os.fstat(stream.fileno()).st_size > disk_ceiling for stream in streams):
            _kill_group(child)
            return Failure(f"wrote more than {disk_ceiling} bytes and was stopped")
        if time.monotonic() >= deadline:
            _kill_group(child)
            return Failure(f"ran longer than {timeout_s}s and was stopped")


def run_program(request: ProgramRequest) -> ProgramOutcome:
    """Run one program and report what happened. Never raises.

    **This is not a sandbox and must not be used as one.** A program run this
    way can read and write anything the person running this project can. What
    it does do is narrower and worth knowing exactly: there is no shell, so
    nothing is interpreted; the environment is built from an allowlist, so no
    secret this project holds is *passed* to it — `HOME` is, and this
    project's secrets live in a file under it, which the "not a sandbox"
    paragraph above already covers; time and output are bounded, so
    nothing runs forever or floods memory. That stops an accident. It stops
    nothing that means harm.

    The deliberate consequence is that deciding to run software this project
    did not write stays a separate decision somebody has to make on purpose —
    it is not granted by this function existing (capability blueprint §7 OQ3).

    `argv` is a sequence and there is no shell, so a caller cannot produce the
    injection CLAUDE.md's `--` rule is about *here*. That rule still binds the
    caller when the program itself reads option-shaped arguments: a value
    starting with `-` is an option to whatever is being run.

    Deliberately no `setrlimit`: it would need `preexec_fn`, which is unsafe in
    the presence of threads, and every `call` node body runs through
    `asyncio.to_thread`. A hardening that can deadlock the thing it hardens is
    worse than its absence (spec.md § Rejected alternatives). `start_new_session`
    is *not* in that category — subprocess implements it inside the child it
    forks rather than through `preexec_fn`.

    "Never raises" has two stated exceptions, both deliberate. A malformed
    value in either config key raises `ValueError` from `config.env_int`,
    which is how every config read in this project behaves on purpose: a bad
    value fails loudly at the one site that reads it. And nothing here catches
    a caller putting a non-string in `argv` — that is a bug in the caller, and
    `run_graph` already reports a body's exception as that node failing.
    """
    if not request.argv:
        return Failure("no program was given to run")
    timeout_s = (
        request.timeout_s
        if request.timeout_s is not None
        else config.env_int("SADANA_EXECUTION_PROGRAM_TIMEOUT_S", 120)
    )
    cap = config.env_int("SADANA_EXECUTION_PROGRAM_OUTPUT_BYTES", 1024 * 1024)

    # Temporary files rather than pipes: `capture_output=True` reads without a
    # ceiling — the exact defect found in `run_http` — and draining two pipes
    # by hand to bound them is how a full pipe buffer deadlocks.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            # `start_new_session` puts the child in its own process group so a
            # timeout can kill what it *spawned* too. Without it only one pid
            # is killed, its children are reparented and keep running —
            # measured: a 2s limit on a program that spawns a 25s sleeper
            # returned in 2s and left the sleeper alive, still holding the
            # capture file. "Nothing runs forever" would have been false for
            # any program that spawns, which is most of what this is for.
            child = subprocess.Popen(  # noqa: S603 - a sequence, never a shell; see the docstring
                request.argv,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                cwd=request.cwd,
                env=_child_env(request.env),
                start_new_session=True,
            )
        except OSError as exc:
            return Failure(f"could not be run: {_safe(str(exc))}")

        stopped = _wait_bounded(
            child, timeout_s=timeout_s, disk_ceiling=cap * _WRITE_CEILING_FACTOR, streams=(out, err)
        )
        if stopped is not None:
            return stopped

        stdout, cut_out = _read_capped(out, cap)
        stderr, cut_err = _read_capped(err, cap)

    return Ran(exit_code=child.returncode, stdout=stdout, stderr=stderr, truncated=cut_out or cut_err)
