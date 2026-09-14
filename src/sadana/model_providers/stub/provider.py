"""stub — a network-free, cost-free provider for development and tests.

H21. A second real member of `model_access`'s provider registry (the first
provider, besides `openrouter`, to declare both `request_fn` and
`stream_fn`): every test and script that needs to exercise streaming without
touching the network or spending real money against OpenRouter uses this one
instead. `env_vars=()` — no credential to configure, ever.
"""

from __future__ import annotations

from sadana.model_access import OnDelta, ProviderManifest, Request, register_provider

_CONTENT = "this is a stub reply"


def request_fn(request: Request) -> tuple[int, dict]:
    del request
    return 200, {
        "choices": [{"message": {"content": _CONTENT}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def stream_fn(request: Request, on_delta: OnDelta) -> tuple[int, dict]:
    # Three fragments, chosen so a test can assert more than one delta
    # arrived without the split itself meaning anything.
    third = len(_CONTENT) // 3
    for fragment in (_CONTENT[:third], _CONTENT[third : 2 * third], _CONTENT[2 * third :]):
        on_delta(fragment)
    return request_fn(request)


register_provider(
    ProviderManifest(
        name="stub",
        env_vars=(),
        request_fn=request_fn,
        stream_fn=stream_fn,
    )
)
