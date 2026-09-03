"""kimi-coding — registered, not wired.

Identity copied from hermes-agent's plugins/model-providers/kimi-coding/__init__.py.
No request_fn: calling sadana.model_access.send() against this provider
raises ProviderNotWired. Porting real request-handling here is future work,
taken on when a caller needs it — see docs/tasks/C1-model-access/intent.md.
"""

from sadana.model_access import ProviderManifest, register_provider

register_provider(
    ProviderManifest(
        name="kimi-coding",
        env_vars=("KIMI_API_KEY", "KIMI_CODING_API_KEY"),
        base_url="https://api.moonshot.ai/v1",
    )
)
