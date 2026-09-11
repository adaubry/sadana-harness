"""EXECUTION — what a call step may do.

Answers `docs/reference/plugin_blueprint.md`'s own narrowed question ("what
may a call node do, and in what?") for exactly one case: a first-party
plugin's own code making one real outward HTTP request, in-process. No
sandboxing (declined — no marketplace, no untrusted source exists yet, see
docs/tasks/E1-call-node-execution/intent.md), no registry (declined — a
family of one earns no dispatch table, see spec.md § Rejected alternatives
and the CLAUDE.md rule this work item's design added).

``run_http`` mirrors ``model_providers/openrouter/provider.py``'s own
``_post`` almost exactly: the standard library only, and a "never raises"
contract — every network-level failure becomes a ``Failure``, never an
exception the caller has to catch.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field

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
