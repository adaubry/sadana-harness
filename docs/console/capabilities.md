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
| `artifacts.download` | downloading a stored artifact's bytes | 0.0.1 (H20) | not gated by a console prompt; declared for completeness |
| `plugins.install` | installing a plugin from a git source | 0.0.1 (H24) | not gated by a console prompt; declared for completeness |
| `plugins.inspect` | reading a plugin's own layout/manifest | 0.0.1 (H24) | not gated by a console prompt; declared for completeness |
| `plugins.write` | editing and saving a plugin | 0.0.1 (H24) | A24 |
| `schedules.write` | creating/editing a cron schedule | 0.0.1 (H27) | A18 |
| `approvals.wait` | a paused `call` node becoming an addressable, resumable resource | 0.0.1 (H18) | not gated by a console prompt; declared for completeness |
| `approvals.call` | resolving a pending approval | 0.0.1 (H18) | not gated by a console prompt; declared for completeness |
| `streaming` | live event/frame delivery over the tether | 0.0.1 (H21) | not gated by a console prompt; declared for completeness |
| `runs.live` | watching a run's progress as it happens | 0.0.1 (H21) | not gated by a console prompt; declared for completeness |
| `runs.stop` | interrupting a run mid-flight | 0.0.1 (H21) | A15 |
| `settings.write` | changing box configuration through the door | 0.0.1 (H14) | not gated by a console prompt; declared for completeness |
| `secrets.write` | `PUT /v1/secrets/{name}` (write-only) | 0.0.1 (H14) | not gated by a console prompt; declared for completeness |
| `upgrade` | `POST /v1/harness/actions/upgrade` | 0.0.1 (H30) | not gated by a console prompt; declared for completeness |

`grammar.v1`/`changes`/`inventory` are the only three H19 itself declared;
every other row above landed with the work item named in "landed in" —
`harness`'s own `upgrade` action was declared in its `NounSpec` from H19
onward (so a request has always named a real action) but the capability
itself stayed off, correctly answering `501`, until H30 turned it on.

The three `plugins.*` rows landed here, in H30's own commit, rather than
in H24's: H24 had not merged onto a shared branch when this table was
filled in (`docs/tasks/H30-tether-enroll-frames-lifecycle/review.md` §
Findings has the full account). `declared()` reports all sixteen of these
names as of this table, now that H14 and H30 have both landed on `main`.
