"""alibaba-coding-plan — registered, not wired.

Identity copied from hermes-agent's plugins/model-providers/alibaba-coding-plan/__init__.py.
No request_fn: calling sadana.model_access.send() against this provider
raises ProviderNotWired. Porting real request-handling here is future work,
taken on when a caller needs it — see docs/tasks/C1-model-access/intent.md.
"""

from sadana.model_access import ProviderManifest, register_provider

register_provider(
    ProviderManifest(
        name="alibaba-coding-plan",
        env_vars=("ALIBABA_CODING_PLAN_API_KEY", "DASHSCOPE_API_KEY", "ALIBABA_CODING_PLAN_BASE_URL"),
        base_url="https://coding-intl.dashscope.aliyuncs.com/v1",
    )
)
