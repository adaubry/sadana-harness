"""plugin-d's node bodies:
docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md.

`start_job` is the `call` node's body — in a real plugin this is where the
external job's own callback target would be set to
``value["_sadana_session_key"]`` (CLAUDE.md: a plugin's own execution
identity crosses into `arguments` only via a `_sadana_`-prefixed reserved
key). No real network call here — this fixture proves the pause/resume
mechanism itself, not `execution.run_http` (already covered by plugin-a).
`summarize` is the `compute` node the `wait` node's own successor names; its
input is whatever the resuming event's payload was, not anything `start_job`
returned — a `wait` node's own output is the answer that arrived, not its
predecessor's.
"""


def start_job(value):
    session_key = value.get("_sadana_session_key", "unknown-session")
    job = value.get("job", "an external job")
    return f"started {job!r} for session {session_key!r}"


def summarize(value):
    return f"the external job answered: {value}"
