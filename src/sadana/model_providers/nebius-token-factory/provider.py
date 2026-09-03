"""nebius-token-factory — registered, not wired.

Identity copied from hermes-agent's plugins/model-providers/nebius-token-factory/__init__.py.
No request_fn: calling sadana.model_access.send() against this provider
raises ProviderNotWired. Porting real request-handling here is future work,
taken on when a caller needs it — see docs/tasks/C1-model-access/intent.md.
"""

from sadana.model_access import ProviderManifest, register_provider

register_provider(
    ProviderManifest(
        name="nebius-token-factory",
        env_vars=("NEBIUS_API_KEY", "NEBIUS_TOKEN_FACTORY_API_KEY", "NEBIUS_BASE_URL"),
        base_url="https://api.tokenfactory.nebius.com/v1",
    )
)
