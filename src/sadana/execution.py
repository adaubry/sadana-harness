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
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return Success(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip() or exc.reason
        return Failure(f"HTTP {exc.code}: {detail[:200]}")
    except (OSError, ValueError) as exc:
        return Failure(str(exc))
