"""The one way to ask sadana-harness for a model's response.

``send(request) -> Outcome`` is the single entry point B2's spec fixed for
CONVERSATION <-> MODEL-ACCESS: one call in, one of six named outcomes out.
MODEL-ACCESS keeps nothing between calls — ``send()`` makes exactly one
attempt and returns; retrying (waiting, incrementing ``attempt``, calling
again) is the caller's job, not this module's.

Providers register themselves by dropping a directory under
``model_providers/`` whose ``provider.py`` calls ``register_provider()`` at
import time — mirrors hermes-agent's own ``providers/__init__.py`` registry,
narrowed to one discovery tier (no pip entry-points, no user directory: no
consumer for either exists yet). A provider with no ``request_fn`` is
registered but not wired; calling ``send()`` against it raises
``ProviderNotWired`` rather than silently doing nothing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sadana import config

# (http_status, parsed_json_body). ``http_status`` is ``None`` for a
# transport-level failure (timeout, connection error) that never reached a
# server — treated the same as a 5xx by ``classify()``.
RawResult = tuple[int | None, dict]


# ── Request / Outcome ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Request:
    """Everything one ``send()`` attempt needs. A value, not a handle into
    CONVERSATION's live state."""

    messages: tuple[dict, ...]
    provider: str
    model: str
    tools: tuple[dict, ...] = ()
    attempt: int = 0  # 0 on a first attempt; the caller passes back whatever a prior `Retry` returned.


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class Response:
    content: str | None
    tool_calls: tuple[dict, ...]
    finish_reason: str
    usage: Usage


@dataclass(frozen=True)
class Retry:
    next_attempt: int  # pass back unchanged as `Request.attempt` on the next call.


@dataclass(frozen=True)
class NeedsCredentialOrProviderChange:
    detail: str


@dataclass(frozen=True)
class NeedsContextCompression:
    detail: str


@dataclass(frozen=True)
class Degenerate:
    detail: str


@dataclass(frozen=True)
class Abort:
    detail: str


Outcome = Response | Retry | NeedsCredentialOrProviderChange | NeedsContextCompression | Degenerate | Abort


class UnknownProvider(Exception):
    """`send()` or `get_provider()` was asked for a name nothing registered."""


class ProviderNotWired(Exception):
    """The provider is registered but has no `request_fn` — not a silent no-op."""


# ── Classification ───────────────────────────────────────────────────────
#
# Status/body driven, deliberately not exception-traceback driven — hermes's
# own error_classifier.py is clean of that pattern; a narrower, related
# pattern in hermes's conversation_loop.py caused two production incidents
# (a transient error misclassified as permanent, then an unbounded retry
# loop). See docs/tasks/C1-model-access/spec.md.

_CONTEXT_LENGTH_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
)
# Signaled refusals are already a complete, terminal response — not an empty
# one — even though they can carry zero completion tokens. Matches hermes's
# own empty_response_guard.py distinction.
_REFUSAL_FINISH_REASONS = ("content_filter",)


def _error_message(body: dict) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message", ""))
    return str(body) if body else ""


def classify(status: int | None, body: dict, *, attempt: int, max_retries: int) -> Outcome:
    """Map one HTTP round trip's result to one of the six closed outcomes."""
    if status in (401, 403):
        return NeedsCredentialOrProviderChange(_error_message(body) or f"status {status}")

    transient = status is None or status == 429 or 500 <= status < 600
    if transient:
        if attempt < max_retries:
            return Retry(attempt + 1)
        return Abort(f"retry cap ({max_retries}) reached: {_error_message(body) or status}")

    message = _error_message(body).lower()
    if status == 413 or any(marker in message for marker in _CONTEXT_LENGTH_MARKERS):
        return NeedsContextCompression(_error_message(body) or f"status {status}")

    if status == 200:
        choices = body.get("choices") or []
        if not choices:
            return Degenerate("200 with no choices")
        choice = choices[0]
        message_obj = choice.get("message") or {}
        finish_reason = choice.get("finish_reason") or "stop"
        usage_raw = body.get("usage") or {}
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
        )
        content = message_obj.get("content")
        tool_calls = tuple(message_obj.get("tool_calls") or ())
        if usage_raw and usage.completion_tokens == 0 and finish_reason not in _REFUSAL_FINISH_REASONS:
            return Degenerate(f"finish_reason={finish_reason}, completion_tokens=0")
        return Response(content=content, tool_calls=tool_calls, finish_reason=finish_reason, usage=usage)

    return Abort(_error_message(body) or f"unclassified status {status}")


# ── Provider registry ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderManifest:
    """Identity, not behavior. Deliberately smaller than hermes's ~15-field
    ProviderProfile — the extra fields there support request-shaping hooks
    this work item isn't porting for 38 of 39 providers. See spec.md."""

    name: str
    env_vars: tuple[str, ...] = ()
    base_url: str = ""
    request_fn: Callable[[Request], RawResult] | None = field(default=None, repr=False)


_REGISTRY: dict[str, ProviderManifest] = {}
_discovered = False
_PROVIDERS_DIR = Path(__file__).resolve().parent / "model_providers"


def register_provider(manifest: ProviderManifest) -> None:
    """Called by each provider's own ``__init__.py`` at import time."""
    _REGISTRY[manifest.name] = manifest


def _discover() -> None:
    """Populate the registry by importing every plugin under model_providers/.

    Lazy — only runs on first ``get_provider``/``list_providers`` call.
    Mirrors hermes's own ``providers/__init__.py`` discovery mechanism,
    narrowed to one tier: the bundled directory only.

    Each provider's manifest file is named ``provider.py``, not
    ``__init__.py`` — hermes uses ``__init__.py``, but hermes's providers are
    never meant to be imported as regular Python packages either (they are
    always loaded via ``spec_from_file_location`` below, never ``import
    plugins.model_providers.foo``). Provider directory names carry hyphens
    (e.g. ``ai-gateway``), which are not valid Python package identifiers;
    ``__init__.py`` inside such a directory makes `mypy`'s `files = ["src"]`
    static package-walk try to build a dotted package name from the
    directory and fail. ``provider.py`` carries no package-boundary meaning
    to mypy, so the hyphenated names never come up.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True
    if not _PROVIDERS_DIR.is_dir():
        return
    for child in sorted(_PROVIDERS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        manifest_file = child / "provider.py"
        if not manifest_file.exists():
            continue
        module_name = f"sadana._model_providers.{child.name.replace('-', '_')}"
        if module_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(module_name, manifest_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)


def get_provider(name: str) -> ProviderManifest:
    _discover()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownProvider(name) from None


def list_providers() -> list[ProviderManifest]:
    _discover()
    return list(_REGISTRY.values())


# ── send() ────────────────────────────────────────────────────────────────


def send(request: Request) -> Outcome:
    """Make exactly one attempt to reach a model. Never retries internally."""
    manifest = get_provider(request.provider)
    if manifest.request_fn is None:
        raise ProviderNotWired(request.provider)

    missing = [v for v in manifest.env_vars if not os.environ.get(v)]
    if missing:
        # Zero-network: the caller shouldn't have to make a doomed request
        # to learn a credential is simply absent.
        return NeedsCredentialOrProviderChange(f"missing env var(s): {', '.join(missing)}")

    max_retries = config.env_int("SADANA_MODEL_ACCESS_MAX_RETRIES", 3)
    status, body = manifest.request_fn(request)
    return classify(status, body, attempt=request.attempt, max_retries=max_retries)


# ── Context window ───────────────────────────────────────────────────────
#
# One static fact for the one model this work item wires — not hermes's
# live models.dev-backed catalog (multi-thousand-line subsystem with a
# background refresh daemon; no second consumer here to justify it yet).

_CONTEXT_WINDOWS: dict[tuple[str, str], int] = {
    # Verified live against OpenRouter's public /api/v1/models catalog
    # (2026-09-03). Hermes's own fallback_models tuple for OpenRouter names
    # "deepseek/deepseek-chat", but that id is no longer served — the
    # catalog has moved to dated "deepseek-v4-*" ids. A static fallback list
    # is exactly this kind of thing to go stale; confirmed live rather than
    # trusted from source.
    ("openrouter", "deepseek/deepseek-v4-flash-0731"): 1_310_720,
}


def context_window(provider: str, model: str) -> int:
    return _CONTEXT_WINDOWS[(provider, model)]
