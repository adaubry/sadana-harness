# The door — wire contract

Written by sadana-harness H19 for the console's own agents. This is the
box's half of a two-repository contract; where this document and the
console's own generated contract (A04) disagree, `grammar.json`'s
`$comment` says how that gets caught and fixed. §1-§4 below are
implemented verbatim by `src/sadana/door/`; nothing in this document is
aspirational except where a section says otherwise.

## §1 Grammar

**Paths.** `GET /v1/{plural}` · `GET /v1/{plural}/{id}` · `POST /v1/{plural}`
· `PATCH /v1/{plural}/{id}` · `DELETE /v1/{plural}/{id}` ·
`POST /v1/{plural}/{id}/actions/{name}` · one level of child nesting:
`/v1/{plural}/{id}/{child_plural}[/{cid}]`. Fixed paths, outside that
grammar: `GET /v1/operations/{id}`, `GET /v1/changes?since=<cursor>&limit=<n>`,
`GET /v1/inventory`, `GET /v1/harness`, `POST /v1/harness/actions/{name}`.

**Ids.** `<prefix>_<uuidv7 hex without dashes>`; prefix two to five lowercase
letters, from a closed registry (`ids.PREFIXES`).

**List params.** Exactly four: `filter`, `order_by` (`<field> asc|desc`,
default `created_at desc`), `page_size` (1-200, default 50), `page_token`.
One tolerated extra: `count=true` adds `"count": <n>` to the response. Any
other name is `400 VALIDATION` naming it.

**Filter grammar.** Fields `a` or `a.b`; operators `= != < <= > >= :` (`:` is
string-only "contains"); boolean `AND OR NOT`, precedence `NOT > AND > OR`;
parentheses; double-quoted strings with `\"` escapes; numbers; ISO-8601
dates; `true`/`false`. Only fields a noun declares filterable; an unknown
field is `400 VALIDATION`.

**Cursors.** `page_token` is an opaque, base64url-encoded token bound to the
`order_by` it was minted under. A token presented under a different
`order_by` is `400 VALIDATION`. Paging is a keyset scan over already-sorted
rows.

**Standard fields**, on every resource: `id`, `created_at`, `updated_at`
(ISO-8601 UTC, millisecond precision, trailing `Z`), `state`, `tags`
(object), `harness_id`, `version` (integer), and `name` where the noun has
one.

**Problem Details** (RFC 9457): `{"type", "title", "status", "code",
"detail"?, "instance"?, "errors"?: [{"field", "code"}]}`,
`Content-Type: application/problem+json`. Exactly thirteen codes:

| code | status |
| --- | --- |
| `UNAUTHENTICATED` | 401 |
| `NOT_FOUND` | 404 |
| `PAYMENT_REQUIRED` | 402 |
| `FORBIDDEN` | 403 |
| `VALIDATION` | 400 |
| `CONFLICT` | 409 |
| `PRECONDITION_FAILED` | 412 |
| `RATE_LIMITED` | 429 |
| `QUOTA_EXCEEDED` | 429 |
| `HARNESS_OFFLINE` | 503 |
| `HARNESS_CAPABILITY_MISSING` | 501 |
| `IDEMPOTENCY_MISMATCH` | 422 |
| `INTERNAL` | 500 |

**Headers in.** `Authorization: Bearer <token>` (every request);
`X-Sadana-Harness: hrn_…` (every request; must equal both the token's `hrn`
claim and this box's own id); `Idempotency-Key` (every `POST` the console
sends, though a bare `POST` with no key is processed and simply not stored
for replay); `If-Match: "<version>"` (every `PATCH`/`DELETE`/action against
an existing resource).

**Headers out.** `ETag: "<version>"` on every single-resource response;
`Retry-After` on any `429`.

**Idempotency.** Same key + same body, for the same principal, replays the
stored status and body byte-for-byte and creates nothing. Same key +
different body is `422 IDEMPOTENCY_MISMATCH`. Keys expire after 24 hours.

**Operations.** Anything the box can't answer within two seconds becomes
`202 {"operation": {"id", "state": "running|succeeded|failed", "resource":
{"noun", "id"} | null, "error": <Problem> | null, "created_at",
"updated_at"}}`; poll `GET /v1/operations/{id}`.

**Actions.** State-gated (`409 CONFLICT`, detail `"<action> requires state
<from-list>; current state is <state>"`), then capability-gated (`501
HARNESS_CAPABILITY_MISSING`, detail naming the capability), then
scope-gated (`403 FORBIDDEN`, detail `"requires scope <noun>:<verb>"`).

**Visibility.** An unknown id and an id the principal may not read are both
`404 NOT_FOUND`. Never `403` for existence.

## §2 The token

ES256 JWT. Header carries `kid`. Claims: `sub`, `org`, `ws`, `hrn`, `scope`
(array of `"<noun>:<verb>"`), `iat`, `exp`, with `exp - iat <= 300` and 30
seconds of leeway. Keys come from a JWKS the console serves at
`<console url>/.well-known/jwks.json`; the box caches it, refreshes at most
once a minute on an unknown `kid`, and keeps an already-fetched key until
every token it could have signed has necessarily expired. Every decode pins
`algorithms=["ES256"]` — no other algorithm is ever accepted.

Refusals: missing or unparseable token → `401`; expired → `401`; `hrn` claim
≠ this box's own id → `403`; `org` claim ≠ the organisation recorded at
enrollment (unset, until H30, means "any") → `403`; `X-Sadana-Harness`
header ≠ the token's `hrn` claim → `403`; a route's required scope absent
from the token's `scope` array → `403` naming it.

The acting account is `"console:" + sub`. Nothing in a request body ever
names an account.

## §3 Events

Rendered today by `GET /v1/changes`; H30 additionally pushes them as frames
over the tether. One shape either way: `{"kind": "changed"|"deleted",
"harness_id", "noun", "id", "state"?, "updated_at", "version"?,
"search_doc"?: {"title", "subtitle"?, "body"?, "facets": {…}}}`.
`tenant_id` is never included — the console's own ingest resolves it from
the harness that delivered the event.

## §4 Capabilities

The closed list, what each gates, and which console prompt (if any) gates
on it: see `docs/console/capabilities.md`.

## §5 Frames

Delivered by H30 (`src/sadana/tether/`), over the one outbound WebSocket
§1's own grammar assumes a transport for. Every frame is a JSON object
with a `"type"` field; an unrecognised one answers with an `error` frame,
never a crash or a silently dropped connection.

- **`challenge`** `{"type": "challenge", "nonce"}` — the relay's first
  frame after the WebSocket upgrade.
- **`challenge_response`** `{"type": "challenge_response", "harness_id",
  "signature"}` — `signature` is base64 of the *raw* r‖s ES256 signature
  (not DER) over the nonce's own UTF-8 bytes, from the box's self-generated
  P-256 key pair.
- **`welcome`** `{"type": "welcome"}` — any other reply to the signed
  challenge closes the connection and backs off.
- **`hello`** `{"type": "hello", "harness_id", "version", "capabilities",
  "ledger_head"}`, sent by the box immediately after `welcome`.
  `capabilities` is computed fresh at connect time from `declared()` —
  never a value fixed earlier in the process — so a reconnect after a
  later capability lands advertises it with no code change on the box.
  `ledger_head` is what the console's own `GET /changes?since=` catch-up
  seeds from after a reconnect; the box does not remember a cursor across
  one itself.
- **`heartbeat`** `{"type": "heartbeat", "at"}`, sent by the box every 15s
  while connected. The box never marks itself degraded or offline — that
  judgment, from missed heartbeats, is the console's alone.
- **`request`** `{"type": "request", "id", "method", "path", "query",
  "headers", "body"}` ↔ **`response`** `{"type": "response", "id",
  "status", "headers", "body"}`, matched by `id`. `body` is a JSON value or
  `null`; a binary body (the artifact download) is base64 with
  `"encoding": "base64"` alongside it. A response body over
  `SADANA_TETHER_MAX_RESPONSE_BYTES` (default 8 MiB) answers as a clean
  `500 INTERNAL` `response` — still correlated by `id` — rather than
  inlining an unbounded blob into one frame.
- **`event`** `{"type": "event", "cursor", "event": <§3's own shape,
  without `tenant_id`>}` — the ledger tail, in order, for as long as the
  connection lasts. A dropped connection is expected to leave a gap; the
  console's own `GET /changes?since=` closes it, not a cursor the box
  remembers across a reconnect.
- **`ephemeral`** `{"type": "ephemeral", "name", "harness_id", "data"}` —
  H21's own queue (`door/events.py`'s `ephemeral_queue`), drained onto the
  wire unchanged. Lossy by design: a delta still queued from before a
  disconnect is discarded, not delivered late, at the moment a new
  connection is established.
- **`error`** `{"type": "error", "code", "detail"}`.

`429` on the WebSocket upgrade itself, with a `Retry-After` header, is
obeyed exactly — the box sleeps that long and nothing else. Any other
disconnection or connect failure reconnects with exponential backoff and
full jitter, capped at 60 seconds; the attempt count resets after a
connection that stayed up at least that long.

## §6 What the console's prompts did not anticipate

Carried here from `docs/reference/console_fit_plan.md` §6 and kept in sync
with it — if that section changes, this one does too, in the same commit.

**Node kinds are seven, not the smaller set the prompts assume.** They are
`compute`, `ask`, `route`, `stop`, `call`, `each` and `wait`. Any console
surface that draws, validates or filters a workflow graph handles all seven,
and `wait` in particular is not an error state — it is the pause that lets
anything waiting on a person be addressable rather than a blocking prompt.

**A credential's value never travels with the thing that uses it.** The
value lives on the box, in `state_dir/.env` at `0600`, write-only (H14,
`docs/tasks/H14-tuned-config-settings-secrets/spec.md`) — `POST
/v1/secrets {name, value, kind?}` creates it and `PATCH /v1/secrets/{id}
{value?}` rotates it, the door's own standard verbs, keyed by the secret's
own minted id like every other noun. `list`/`get` answer a fingerprint —
the last four characters of the value, a middle dot, the first eight hex
characters of its SHA-256 — computed live on every call, never stored,
never the value itself. Anywhere a plugin, a workflow or a config refers to
a credential, it carries a `credential_ref` (or `secret_ref`) that names
the secret by its own `name` field, never by the id `POST` minted for it —
a real, already-exported environment variable satisfies that reference
exactly as well as one written to `.env` through this noun, since the
environment always wins; a literal value in that position is a validation
failure, not a convenience.

**The harness `exit_reason` vocabulary is eight values, and the console's is
five.** The harness reports `completed`, `budget_exhausted`,
`wall_clock_exhausted`, `persistence_failed`, `provider_failed`,
`context_overflow_unhandled`, `interrupted` and `invalid_tool_calls`. The
mapping onto the console's five is the box's own job, done before it
answers — the box translates before it answers, so the console never learns
eight names and the harness never loses the distinction between running out
of turns and running out of clock.

**A promoted `messages.create` operation carries `resource: null`.** Found
during H20, not anticipated by any prompt: the door's own `run_bounded`
computes a create's `resource` once, from the URL's own id segment, before
the slow call ever runs — and a create path carries no id segment to compute
one from. Read the assistant's reply from the conversation's own messages
once the operation settles (`succeeded` or `failed`), the same call the
message list already serves. H30, which reopens the door's framework, is
where a create-time resource hint gets added and this note retires.
