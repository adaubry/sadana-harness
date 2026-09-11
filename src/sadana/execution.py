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
        return Failure(f"unsupported URL scheme: {request.url!r} (only http/https are allowed)")
    timeout_s = config.env_int("SADANA_EXECUTION_HTTP_TIMEOUT_S", 30)
    try:
        req = urllib.request.Request(request.url, data=request.body, headers=request.headers, method=request.method)
        with _open(req, timeout_s) as resp:
            return Success(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip() or exc.reason
        return Failure(f"HTTP {exc.code}: {detail[:200]}")
    except (OSError, ValueError) as exc:
        return Failure(str(exc))
