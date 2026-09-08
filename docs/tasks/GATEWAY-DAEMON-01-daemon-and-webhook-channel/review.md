# Review: GATEWAY-DAEMON-01 — daemon and webhook channel (from plan.md 2026-09-08)

Reviewed: 7c29e5a..working tree (nothing committed yet for this item) — 20 files, +1679/-46
Reviewer context: fresh subagent (general-purpose, no prior context beyond intent.md/spec.md/plan.md/CLAUDE.md and the diff) — delegated per deploy-skill's own instruction, since this session wrote the code under review.
Second opinion: none beyond that — `/ponytail-review` + `/simplify` ran during build as a self-check, not a substitute for this cold review.

## Evidence

```
$ make verify
docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck................................................................Passed
Detect secrets............................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 22 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 72%]
........................................................................ [ 90%]
.....................................                                    [100%]
397 passed in 4.76s
TESTS OK
VERIFY OK
```

```
$ python scripts/prove_gateway_webhook_e2e.py
waiting for the gateway to bind its socket...
[ok] gateway is listening on 127.0.0.1:18765

=== turn 1: first message on a new chat_id ===
status=200 body={'ok': True, 'text': 'echo: hello'}
[ok] first response is a real, successful reply

=== turn 2: second message, same chat_id — proves continuity ===
status=200 body={'ok': True, 'text': 'echo: still there?'}
[ok] second turn continued the same conversation

=== wrong secret ===
status=401 body={'ok': False, 'text': 'unauthorized'}
[ok] a wrong secret is rejected with 401

=== SIGTERM lets an in-flight request finish ===
sending SIGTERM to pid 404034 while a slow request is in flight...
status=200 body={'ok': True, 'text': 'slow reply'}
[ok] SIGTERM let the in-flight request finish before the process stopped

ALL ASSERTIONS PASSED
[ok] gateway_daemon.run() returned 0 after a clean stop
```

## Findings

Five Important findings and two Nits below, from a fresh-context cold review (a delegated subagent with no prior context beyond the diff and the three artifacts) — three bugs/security, two artifact-chain compliance gaps against plan.md.

### Important

- **[Bugs] — FIXED.** `src/sadana/channel_webhook.py:45,94` — the webhook secret header was looked up case-sensitively. `do_POST` converts the case-insensitive `self.headers` (an `email.message.Message`, case-insensitive by HTTP spec, RFC 7230 §3.2) into a plain `dict(self.headers)` (line 94) before `parse_webhook_request()` did an exact-case `headers.get(_SECRET_HEADER, "")`. The cold reviewer confirmed this directly against a live `make_server()` instance: a request carrying the *correct* secret value under a different header casing (`x-sadana-webhook-secret`) was rejected `401`. Every test in `tests/unit/test_channel_webhook.py` and the proof script happened to construct headers with the exact literal casing, so this never surfaced. Fix: `channel_webhook.py` now has a `_header()` helper doing a case-insensitive lookup over the mapping, used for the secret check; `tests/unit/test_channel_webhook.py::test_parse_webhook_request_secret_header_lookup_is_case_insensitive` is the new regression test. `make verify` re-run clean afterward (398 passed, `VERIFY OK`).

- **[Security]** `src/sadana/channel_webhook.py:85-94` — `do_POST` reads the whole request body (`self.rfile.read(length)`, trusting a client-supplied `Content-Length`) *before* the secret check runs. `ThreadingHTTPServer` spawns one unbounded thread per connection with no cap, so an unauthenticated caller that can reach the port can force each connection's thread to block or buffer on an arbitrarily large or slow body — pre-auth, no secret required. spec.md's own Lifecycle section treats `--host 0.0.0.0` as "a real decision made once" for genuine external reachability, so this isn't purely a localhost-only concern. Consequence: a real gap in the one place spec.md said this process should be conservative ("a process that can trigger a model call and run plugin bodies").

- **[Compliance]** `src/sadana/model_access.py:42-46` is not in plan.md's `## Files that change`. The diff adds `DEFAULT_PROVIDER`/`DEFAULT_MODEL` module constants there and rewires `subcommands/chat.py`'s `cmd_chat` to read them — beyond what plan.md's `chat.py` entry promised ("drop the three moved names, import them from `sadana.persona` instead. No other behavior change."). This landed during the build-stage `/simplify` self-check (deduplicating a literal that this work item's own new `gateway.py` copy-pasted from `chat.py`), which is a legitimate improvement, but plan.md was never updated to say so. Consequence: the artifact chain no longer fully accounts for a real source file this work item edited.

- **[Compliance]** `tests/unit/test_subcommands_gateway.py` is not in plan.md's `## Files that change`, and plan.md's Order-of-work step 6 only mentions verifying `test_cli.py`, not adding a new gateway-subcommand parser test file. The test itself is reasonable and mirrors `test_build_chat_parser_defaults_and_wiring`'s existing precedent, but plan.md doesn't say it exists. Consequence: same as above — a real test file the plan doesn't name.

- **[Compliance]** `src/sadana/gateway_dispatch.py:23` (module-level `_conn_lock`) contradicts what plan.md explicitly committed to. plan.md's step 6 states in its own words: *"This `threading.Lock()` is the load-bearing addition spec.md's Interface section doesn't show, and it stays,"* describing a lock inside `cmd_gateway_run`'s `on_message` closure in `subcommands/gateway.py`. The actual code has no lock there (`src/sadana/subcommands/gateway.py:36-37`) — the `/simplify` altitude-review pass (during build) found the lock was misplaced and moved it into `gateway_dispatch.py`'s own module scope instead, since that module is spec.md's designated single bridge every channel event routes through. Functionally this is an improvement (a true process-wide serialization owned by the one chokepoint, rather than something every future caller must remember to reimplement), and it was applied inside build-skill's own self-check step — but plan.md's text was never amended to match, and this relocation isn't among plan.md's own enumerated "four corrections" to spec.md's Interface. Consequence: plan.md as written no longer describes where the code actually put its own most-explicitly-flagged addition.

### Nits

- **[Security]** `src/sadana/channel_webhook.py:99` — the `500` handler returns the raw exception string verbatim (`self._reply(500, ok=False, text=str(exc))`), which can leak internal detail (paths, object state) to a caller that already holds the shared secret. Low severity since it requires a valid secret to reach; a generic message would be safer.
- **[Bugs]** `src/sadana/subcommands/gateway.py:29` — `cmd_gateway_run` opens `conn` via `conversation_store.open_store(...)` but never explicitly closes it on `gateway_daemon.run()`'s clean return, unlike `cmd_chat`'s `with closing(...)`. Not observed to cause data loss (each turn is committed via `conversation_store.save`), but it's an asymmetry with the pattern `chat.py` already established.

## What was checked and found clean

- **Acceptance criteria** (spec.md): each checked against a specific artifact — `session_key_for()` in `tests/unit/test_gateway.py`; conversation continuity (`next_turn_seq == 2`) in `tests/unit/test_gateway_dispatch.py::test_handle_inbound_continues_the_same_conversation_across_calls`; `parse_webhook_request()`'s three branches in `tests/unit/test_channel_webhook.py` (no socket); secret-unset refusal and flock double-acquire in `tests/unit/test_gateway_daemon.py` (confirmed the flock test actually exercises real kernel `flock` semantics across two file descriptors); persona extraction moved verbatim (`tests/unit/test_persona.py`). `scripts/prove_gateway_webhook_e2e.py` was re-run directly and passed every assertion (real socket, real HTTP round trip, real `SIGTERM`, in-flight request finishing) — its output is pasted above under `## Evidence`.
- **plan.md's four claimed Interface-pseudocode deviations**: all four verified present exactly as described — `session_key_for() -> str` with no `conversation.py` import; `handle_inbound()`/`on_message` returning `tuple[bool, str]`; `daemon_threads` left at the stdlib default `False` (no `ThreadingHTTPServer` subclass exists in `make_server()` at all, which trivially satisfies this).
- **spec.md's Rejected Alternatives vs. the diff**: no channel-adapter registry or discovery mechanism; no abstract `ChannelAdapter` base class; the exchange is synchronous request-in/reply-out with no background task; default bind host is `127.0.0.1`; persona loading is extracted and shared, not duplicated. No drift found against any of the five rejected alternatives.
- **The five design principles**: reference checked first (spec.md's own Design section cites and reasons about hermes's `systemd_notify.py`, `status.py`'s flock, `session.py::build_session_key`, `platforms/base.py`, and the diff's own `channel_webhook.py` docstring separately corrects a verbatim-copy hazard found in hermes's A2A adapter — `daemon_threads`); the registry-of-one bet was explicitly declined; nothing was added to CONVERSATION/CONTEXT core that belongs in a plugin, and no premature plugin seam was invented; the scenario ("sadana cannot be reached asynchronously") is caught by adding one new process/entry point, the cheapest of the three available moves, not by loading down an existing step; state inventory matches spec.md's own accounting (the `flock`, the shutdown `threading.Event`, plus the newly-relocated `_conn_lock` which is in-memory, process-lifetime, and not persisted — no new persisted state).
- **CLAUDE.md rules**: real-I/O-is-its-own-module honored (`gateway.py` pure; `gateway_dispatch.py` glue; `channel_webhook.py`/`gateway_daemon.py` I/O); names-not-pointers honored (`session_key_for()` is a pure function of the envelope); `conversation_store.bind_persist()` rebuilt fresh per call, never reused across turns; `hmac.compare_digest` used for the secret comparison as spec.md's Concerns section promised.
- **Test isolation**: `tests/conftest.py`'s autouse `_isolated_state` fixture rewrites `SADANA_STATE_DIR` per test, so the two `gateway_daemon.py` tests touching a real `fcntl.flock` cannot collide with each other or with unrelated test files.

## Decision

Approved by Adam, 2026-09-08, with the case-sensitivity Important finding
fixed in this branch before merge (see the finding above — `_header()` in
`channel_webhook.py`, plus its regression test). The remaining four
Important findings (pre-auth body read, the two undocumented plan.md
file-list gaps, and the `_conn_lock` relocation) and both Nits were not
required to be fixed before this approval; they were reviewed, not waived —
carry them forward as-is unless a later work item addresses them.
