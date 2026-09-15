# Capabilities

The closed sixteen (`src/sadana/door/capabilities.py`'s `ALL`). `declared()`
returns the subset the running box actually turns on; a work item's whole
contribution to that set is appending its own name, never removing or
reordering one already there. "Landed in" is `sadana.__version__` at the
artifact that first added the name to `DECLARED` — `—` for a name in `ALL`
but not yet declared by any shipped work item.

| capability | what it gates | landed in | console prompt |
| --- | --- | --- | --- |
| `grammar.v1` | the door itself answers requests at all | 0.0.1 (H19) | not gated by a console prompt; declared for completeness |
| `changes` | `GET /v1/changes` | 0.0.1 (H19) | not gated by a console prompt; declared for completeness |
| `inventory` | `GET /v1/inventory` | 0.0.1 (H19) | not gated by a console prompt; declared for completeness |
| `artifacts.download` | downloading a stored artifact's bytes | — | not gated by a console prompt; declared for completeness |
| `plugins.install` | installing a plugin from a git source | 0.0.1 (H24) | not gated by a console prompt; declared for completeness |
| `plugins.inspect` | reading a plugin's own layout/manifest | 0.0.1 (H24) | not gated by a console prompt; declared for completeness |
| `plugins.write` | editing and saving a plugin | 0.0.1 (H24) | A24 |
| `schedules.write` | creating/editing a cron schedule | — | A18 |
| `approvals.wait` | a paused `call` node becoming an addressable, resumable resource | — | not gated by a console prompt; declared for completeness |
| `approvals.call` | resolving a pending approval | — | not gated by a console prompt; declared for completeness |
| `streaming` | live event/frame delivery over the tether | — | not gated by a console prompt; declared for completeness |
| `runs.live` | watching a run's progress as it happens | — | not gated by a console prompt; declared for completeness |
| `runs.stop` | interrupting a run mid-flight | — | A15 |
| `settings.write` | changing box configuration through the door | — | not gated by a console prompt; declared for completeness |
| `secrets.write` | `PUT /v1/secrets/{name}` (write-only) | — | not gated by a console prompt; declared for completeness |
| `upgrade` | `POST /v1/harness/actions/upgrade` | — | not gated by a console prompt; declared for completeness |

`grammar.v1`/`changes`/`inventory` are the only three this box declares as
of H19; `harness`'s own `upgrade` action is already declared in its
`NounSpec` (so a request names a real action) but the capability itself
stays off until H30, which is what makes the action correctly answer `501`
today rather than doing anything.
