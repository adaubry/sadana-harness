"""huggingface — registered, not wired.

Identity copied from hermes-agent's plugins/model-providers/huggingface/__init__.py.
No request_fn: calling sadana.model_access.send() against this provider
raises ProviderNotWired. Porting real request-handling here is future work,
taken on when a caller needs it — see docs/tasks/C1-model-access/intent.md.
"""

from sadana.model_access import ProviderManifest, register_provider

register_provider(
    ProviderManifest(
        name="huggingface",
        env_vars=("HF_TOKEN",),
        base_url="https://router.huggingface.co/v1",
    )
)
