"""plugin-a's node bodies: docs/tasks/G3-real-plugin-under-eval/spec.md.

`fetch_webhook` is the `call` node's body — the one step in this plugin
that reaches outside the process, gated by F1's approval before it ever
runs. `classify` is the `route` node's body, reading the child's own
reply for the same acknowledgement marker the script used to check
inline.
"""

from sadana import execution, plugins


def fetch_webhook(_value):
    outcome = execution.run_http(execution.HttpRequest(method="GET", url="https://example.com"))
    if isinstance(outcome, execution.Success):
        return plugins.Artifact(kind="link", name="webhook", ref="https://example.com")
    return "webhook fetch failed; falling back to a plain note"


def classify(value):
    return "acknowledged" if "ACKNOWLEDGED" in str(value) else "not_acknowledged"
