"""custom — registered, not wired.

Identity copied from hermes-agent's plugins/model-providers/custom/__init__.py.
No request_fn: calling sadana.model_access.send() against this provider
raises ProviderNotWired. Porting real request-handling here is future work,
taken on when a caller needs it — see docs/tasks/C1-model-access/intent.md.
"""

from sadana.model_access import ProviderManifest, register_provider

register_provider(
    ProviderManifest(
        name="custom",
        env_vars=(),
        base_url="",
    )
)
