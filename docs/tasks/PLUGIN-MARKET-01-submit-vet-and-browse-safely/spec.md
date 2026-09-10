# Spec: A plugin nobody can find is a plugin nobody uses

Intent: docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/intent.md

## Requirements

1. Anyone can submit a plugin release (a repository URL and a tag) for
   consideration — not only the person running this instance. (Intent,
   Proposed outcome ¶1; Affected users — Creators.)
2. Submitting fetches and verifies the tag exactly as installing already
   does, and captures the plugin's declared shape — without ever
   executing a line of the plugin's own code. (Intent, Constraints ¶1;
   Proposed outcome ¶1.)
3. One or more reviewers, all at the same trust level, can see any
   submitted release regardless of its status and decide: approve it,
   reject it with a reason the creator can see, or leave it undecided.
   (Intent, Proposed outcome ¶2; Affected users — Reviewers.)
4. Every release goes through its own review. A creator's earlier
   approval never exempts a later release. (Intent, Constraints, last
   bullet.)
5. An ordinary browser only ever sees a plugin's most recently *approved*
   release. A plugin with no approved release yet is invisible to them,
   and an approved release stays visible even while a newer one is
   pending. (Intent, Proposed outcome ¶2–3.)
6. Tagging a new release on a repository already known to the marketplace
   triggers its submission by itself — nobody runs a command by hand for
   it to be reviewed. (Intent, Proposed outcome, last sentence.)
7. Only public repositories; no credential of any kind anywhere in the
   fetch path. (Intent, Constraints.)
8. No accounts or logins for creators, reviewers, or browsers. (Intent,
   Constraints.)
9. This is not a web application and does not build one; it leaves behind
   data and functions a future, separate web application can read and
   call. (Intent, Constraints.)

## Design

**Where this sits.** Builds directly on PLUGIN-INSTALL-01's closed work:
its tag-fetch-and-verify mechanism, and the PLUGINS block's existing
`Manifest`/`Entry`/`Node` types, which already are the round-trippable,
data-only shape `plugin_blueprint.md` §5.2 designed for exactly this
purpose. Three already-closed files get small, narrow, single-purpose
extensions (below, each justified on its own); everything else is new.

**The central finding this design turns on.** `plugin_manifest.validate()`
(D2, closed) — the only existing function that turns a `plugin.toml` plus
its directory into something inspectable — resolves a `call`/`route`
node's `body` by *importing and executing* `init.py`
(`_load_body_module()`: `importlib.util.module_from_spec` +
`spec.loader.exec_module`). That is an accepted, contained risk today
(`plugin_blueprint.md` §10 Risk 1) only because `validate()`'s one real
caller, `discover_plugins()`, only ever scans plugins already sitting
under `_plugins_root()` — already fetched, already first-party by this
project's current posture. **This work item is the first to hand
`validate()`-shaped logic something nobody has ever looked at.** Reusing
`validate()` as-is would execute a stranger's code to produce a listing —
precisely what intent.md's Problem section names as the danger. Every
other step `validate()` runs is genuinely inert: `_check_schema()` only
`json.loads`s and JSON-Schema-validates a file; `_check_skill()` only
reads a SKILL.md as text; duplicate-name, dangling-target, unreachable,
and cycle checks only walk the declared graph. **Fix**: `validate()`
gains one parameter, `check_bodies: bool = True`. Its existing loop
calling `_check_body()` is wrapped in `if check_bodies:`. The default
preserves `discover_plugins()`'s exact current behavior (one caller, no
change); this work item's own fetch-for-review path calls
`validate(plugin_dir, check_bodies=False)`. Every structural guarantee
survives; only "does this string resolve to a real Python callable" is
skipped — which display never needed anyway, since showing a node's
declared `body`/`skill` string is not the same claim as showing that it
works.

**Reused, not duplicated, from `plugin_install.py` (closed).** `install()`
already does "resolve a tag's real commit, clone it, prove `HEAD` matches"
inline. This item needs exactly that and nothing past it — no manifest
name check, no placement under `_plugins_root()`. Rather than reach into
`install()`'s three private helpers separately, `plugin_install.py` gains
one new public function, `fetch_verified_tag(repo_url, tag, dest) ->
FetchedTag | TagMismatch | FetchFailed`, extracted from `install()`'s own
resolve→clone→verify sequence — and `install()` is refactored to call it
too, so there is one implementation of "fetch this tag and prove it," not
two (CLAUDE.md: "no second way to do any of the above"). `install()`'s own
tests already cover this path; they continue to assert the same outcomes
through the refactor.

**The safe shape is the `Manifest`, serialized, not a new format.**
`plugins.py` gains one pure function, `manifest_to_dict(manifest) ->
dict[str, object]` — a direct field walk of the already-frozen
`Manifest`/`Entry`/`Node` dataclasses into JSON-safe primitives. No new
vocabulary: what a browser or a reviewer sees is exactly the plugin's own
declared name, version, description, entries (tool/purpose/parameters/
start), and nodes (name/kind/body/skill/next/ports) — boxes and arrows,
per `plugin_blueprint.md` §3.5, because that is already what a `Manifest`
is.

**New module: `src/sadana/marketplace.py`** (I/O — SQLite + orchestrates a
temp clone via `plugin_install.fetch_verified_tag()` +
`plugin_manifest.validate(check_bodies=False)`; its own file per
CLAUDE.md's real-I/O rule). One table, in the same database
`conversation_store` already owns, same no-second-writer posture
PLUGIN-INSTALL-01 and OBSERVABILITY-01 both already established:

```sql
CREATE TABLE IF NOT EXISTS marketplace_releases (
    plugin_name    TEXT NOT NULL,
    tag            TEXT NOT NULL,
    repo_url       TEXT NOT NULL,
    revision       TEXT NOT NULL,
    manifest_json  TEXT NOT NULL,
    status         TEXT NOT NULL,   -- 'pending' | 'approved' | 'rejected'
    reason         TEXT,
    submitted_at   REAL NOT NULL,
    decided_at     REAL,
    PRIMARY KEY (plugin_name, tag)
);
```

Only the parsed `Manifest`'s data is kept (`manifest_json`); the temp
clone is deleted the moment it is read, the same posture
`plugin_install.py`'s own tempdir-then-discard-on-failure already has —
nothing about showing a plugin's shape ever needs its raw files again.

A submission's `plugin_name` is never chosen at submission time — it is
whatever the fetched `plugin.toml` itself declares, closing off the whole
class of bug PLUGIN-INSTALL-01 needed `NameMismatch` for. A name is bound
to whichever `repo_url` first submitted it (any status, even rejected —
first claim wins), found by querying the earliest row for that name rather
than storing a second table for it (guideline 4): a later submission
declaring the same name from a *different* repository is refused.

```python
FetchedForReview = ...              # internal only, not returned to callers
SubmitOutcome = Pending | NameOwnedByAnotherRepo | AlreadySubmitted | TagMismatch | FetchFailed | InvalidManifest
DecideOutcome = Decided | UnknownRelease | ReasonRequired | AlreadyDecided

def submit(conn, repo_url: str, tag: str, *, now: float) -> SubmitOutcome: ...
def decide(conn, plugin_name: str, tag: str, decision: Literal["approved", "rejected"], *, reason: str | None, now: float) -> DecideOutcome: ...
def latest_approved(conn, plugin_name: str) -> ReleaseListing | None: ...
def pending_releases(conn) -> tuple[ReleaseListing, ...]: ...
def approved_plugin_names(conn) -> tuple[str, ...]: ...
```

A submission whose manifest doesn't structurally validate
(`InvalidManifest`) is recorded as `status='rejected'` with a
system-generated reason (the validation outcome's own detail) and never
enters the reviewer queue — there is nothing for a human to judge in a
`plugin.toml` that doesn't parse or has a dangling node; that is a
mechanical fact, not a trust decision. This is inferred, not something
intent.md states directly — flagged in Concerns.

`decide()` refuses a `(plugin_name, tag)` that already has a decision
(`AlreadyDecided`) rather than overwriting it. Intent.md's own Open
Questions section left "can an approval be taken back" explicitly
unresolved; refusing any re-decision is the reading of that gap that adds
the least new behavior — a later work item can open take-backs
deliberately, as its own decision, rather than this one making it by
omission.

**New module: `src/sadana/marketplace_webhook.py`** (I/O — HTTP). Mirrors
`channel_webhook.py`'s exact shape: `parse_webhook_request(body, headers,
secret) -> ReleaseSubmission | WebhookUnauthorized | WebhookBadRequest`
and `make_server(host, port, *, secret, on_release) ->
ThreadingHTTPServer`. Payload is this project's own, not a specific git
host's native format: `{"repo_url": "...", "tag": "..."}`, checked against
an `X-Sadana-Marketplace-Webhook-Secret` header the same
`hmac.compare_digest` way. `channel_webhook.py`'s private `_header()`
case-insensitive lookup is promoted to `gateway.py` (already the shared,
pure home for both channels' common types) as `header_value()` — two real
callers now justify the move; `channel_webhook.py` is updated to import it
rather than keep its own copy.

Reference corpus checked for this specific piece: `hermes_cli/webhook.py`
(PLUGIN-SYSTEM-adjacent, production-code) is hermes's own incoming-webhook
gateway — but for subscribing to *outgoing-event* platforms (Telegram,
Discord, Slack, a generic "github_comment" notifier), not for ingesting a
plugin release; hermes has no release-webhook/publish pipeline anywhere in
its own `plugin_index.py`/`plugins_cmd.py` to adopt or decline — that
index is a read-only, centrally-curated JSON file, never something a
creator's own tag push writes to. The one genuinely reusable idea found
there: hermes signs its own webhook payloads with an HMAC-over-body
(`X-Hub-Signature-256: sha256=<hmac(secret, body)>`, mimicking GitHub's
convention) rather than a bare shared-secret header. Declined here anyway
— hermes's reason for that scheme is interop with real GitHub payloads
passed through its gateway largely unmodified; this project's own webhook
payload is already a from-scratch, host-agnostic format no external party
signs, so HMAC-over-body buys tamper-evidence this project doesn't
currently need, at the cost of a second, different auth scheme alongside
`channel_webhook.py`'s already-working one. Reusing the existing bare-
secret-header check keeps one way to authenticate an incoming webhook, not
two.

**Hosting the webhook: generalizing `gateway_daemon.run()`, not
duplicating it.** `gateway_daemon.run()`'s lock/signal/serve-and-shutdown
body has nothing channel-specific in it except that it hardcodes
`channel_webhook.make_server(...)` and a fixed lock filename. This work
item needs the identical lifecycle for a second, independent listener —
exactly the "second real member" CLAUDE.md's own registry-seam rule asks
for before generalizing. `run()`'s signature becomes:

```python
def run(*, make_server: Callable[[], http.server.ThreadingHTTPServer], lock_filename: str = "gateway.lock") -> int
```

`host`/`port`/`secret`/`on_message` and the "secret unset → refuse" check
move to each caller, which is more correct anyway — the daemon's own
lifecycle never had any business knowing what a "secret" is; that was
`channel_webhook`-specific validation leaking into a generic module.
`subcommands/gateway.py`'s one caller (`cmd_gateway_run`) is updated to
build its own server via a closure and pass it in — its runtime behavior
is unchanged. A new, independent `sadana marketplace serve-webhook`
becomes the second caller, passing `lock_filename="marketplace.lock"` so
both daemons can run concurrently on one machine without contending for
the same lock.

**New module: `src/sadana/subcommands/marketplace.py`** (CLI):

```
sadana marketplace submit <repo-url> <tag>
sadana marketplace list [--pending]
sadana marketplace show <plugin-name> [--pending] [--json]
sadana marketplace approve <plugin-name> <tag>
sadana marketplace reject <plugin-name> <tag> <reason>
sadana marketplace serve-webhook [--host] [--port]
```

There is no separate "become a reviewer" step — `--pending` is how anyone
chooses, for that one command, to look at what an ordinary browse would
never show. This is requirement 8's "role is whichever action someone
takes" applied literally: the flag *is* the role declaration, not a
permission check.

### State inventory (guideline 4)

| State | Why it's stored, not derived |
| --- | --- |
| `marketplace_releases` rows | The record of what was submitted, its captured shape, and its decision — none of it exists anywhere else once the temp clone is deleted. |
| Which repository owns a plugin name | **Not stored separately** — derived by querying the earliest row for that name. |
| Whether a release is "the latest approved" | **Not stored as a flag** — derived per query (`status='approved'`, most recent `decided_at`), so approving a later release never requires updating an earlier row. |

## Interface

CLI and Python surfaces above. `SubmitOutcome`/`DecideOutcome` are closed
dataclass sets, never raised exceptions, matching `plugin_manifest.py`'s
`ManifestOutcome` and `plugin_install.py`'s own outcome types. The webhook
handler and the `submit` CLI command both call `marketplace.submit()`
directly — one implementation, two callers, same posture `install()`
already set for CLI/scripts.

## Acceptance criteria

- [ ] Submitting a real (locally-fixtured) tagged release places one
      `pending` row with the manifest's own declared name, not a
      caller-supplied one.
- [ ] A second submission of the same name from a different repository is
      refused (`NameOwnedByAnotherRepo`); a resubmission of the exact same
      `(name, tag)` is refused (`AlreadySubmitted`).
- [ ] A release whose manifest doesn't structurally validate is recorded
      `rejected` automatically and never appears in `pending_releases()`.
- [ ] `submit()` never imports or executes anything from the fetched
      repository — proven by a fixture plugin whose `init.py` would raise
      or have an observable side effect on import, submitted successfully
      anyway.
- [ ] A reviewer can see a pending release's full shape
      (`sadana marketplace show <name> --pending`); an ordinary browse of
      the same name (no flag) shows nothing until it's approved.
- [ ] Approving a release makes it the one an ordinary browse returns;
      approving a newer release replaces it without touching the old row's
      own `approved` status.
- [ ] Rejecting without a reason is refused (`ReasonRequired`); rejecting
      with one records it, retrievable by `show`.
- [ ] Deciding an already-decided release is refused (`AlreadyDecided`).
- [ ] A real webhook POST (secret header, JSON body) reaches the exact
      same outcome as the equivalent `submit` CLI call.
- [ ] `sadana gateway run` and `sadana marketplace serve-webhook` can run
      at the same time on one machine without lock contention.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] A standalone script performs one real submission against a genuine
      public repository over the network, its output pasted as this work
      item's Deploy-stage Evidence, per CLAUDE.md's standing rule.

## Non-goals

- Actually installing a plugin found through the marketplace onto the
  browsing person's own instance. PLUGIN-INSTALL-01's `register`/`install`
  already do this; a person (or a future webapp) uses the repository URL
  and tag the marketplace surfaced, directly.
- Any web application or UI — this item's whole output is data and
  functions a later, separate artifact reads.
- Search, ranking, or categorization beyond a flat list of approved names.
- Revoking an already-approved release. Left exactly as open as intent.md
  left it.
- Multi-reviewer consensus. Any one reviewer's decision stands alone.
- Authenticated identity for any role, or recording who decided something.

## Open questions

- Carried from intent.md, unchanged: whether an approved release can later
  be taken back is not decided here.

## Rejected alternatives

- **Reusing `plugin_manifest.validate()` unchanged** — declined; it
  executes a plugin's own code, which is the one thing this entire work
  item exists to avoid doing to something unreviewed.
- **A new, duplicate daemon-lifecycle module for the webhook** — declined
  in favor of generalizing `gateway_daemon.run()`, since a second real
  need for that exact lifecycle now exists (the rule this project already
  applies to registries applies here too).
- **Hosting the marketplace webhook on the existing chat-webhook endpoint**
  (distinguishing payloads by field-sniffing) — declined; conflates two
  unrelated concerns behind one URL and one secret.
- **Parsing a specific git host's native webhook format** (e.g. GitHub's
  HMAC-signed payload) — declined; would make this feature tied to one
  host where every other piece of the plugin chain treats a repository URL
  as a plain, host-agnostic string. A creator's own tooling calls sadana's
  own simple format instead, the same choice `channel_webhook.py` already
  made for chat messages rather than mimicking a specific chat platform.
- **Storing a submission's raw fetched files** — declined; only the parsed
  `Manifest`'s data is retained, avoiding a staged-files lifecycle nothing
  ever needs again.
- **A second table tracking which repository owns a plugin name** —
  declined; derived from the earliest submitted row instead (guideline 4).
- **Recording who reviewed something** — declined; nothing in intent.md
  asked for it, and there is no authenticated identity to record honestly.
- **HMAC-over-body webhook signing, hermes's own `hermes_cli/webhook.py`
  pattern** — declined; solves interop with a real external signer this
  project's own from-scratch payload format doesn't have, at the cost of a
  second auth scheme alongside `channel_webhook.py`'s already-proven bare
  shared-secret header.

## Concerns

- **The `plugin_manifest.validate()` finding is the load-bearing part of
  this document.** A reviewer should independently confirm
  `check_bodies=False` truly reaches zero execution — re-read
  `_check_schema`/`_check_skill`/the structural checks, not just this
  spec's claim about them, before trusting that path with a genuinely
  untrusted submission.
- **This again reaches into `plugin_blueprint.md` §9's explicitly deferred
  boundary** ("the marketplace: upload, discovery, the release webhook"),
  on top of PLUGIN-INSTALL-01's own earlier expansion into the same
  boundary. Intent.md's "Changed during planning" records that a 2-or-3-
  item split was explicitly recommended and explicitly declined by the
  user — this spec does not re-argue that choice, only flags it again for
  whoever reviews this at Deploy.
- **Auto-rejecting a structurally invalid manifest without human review**
  is this spec's own inference, not a line item intent.md stated. Worth a
  second look: a reasonable alternative is showing it to reviewers anyway,
  labeled as broken, in case a reviewer wants to see *why* rather than
  just that it failed.
- **Three already-closed files get touched**: `plugin_manifest.py` (one
  new parameter, default-preserving), `plugin_install.py` (one function
  extracted, `install()` refactored to call it), and
  `gateway_daemon.py`/`subcommands/gateway.py` (a signature
  generalization, one caller updated). None of these edit those work
  items' own artifacts (`plan.md`/`spec.md`/`review.md` stay untouched,
  same as every prior block-extension in this project's history) — only
  their code, which is the normal way later work builds on earlier work.
  Still worth a reviewer's explicit attention given how much this single
  work item touches at once.
- **Scope, restated plainly**: this is a large work item — a
  security-relevant refactor of existing validation, a new submission/
  review workflow, and a new standing network service, all landing
  together. That was the user's own explicit choice after a recommended
  split was declined; not re-litigated here, but a large surface for one
  Deploy-stage cold review to catch everything in.
