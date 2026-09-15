# Review: The tether — enroll, connect, and be reachable (from plan.md 2026-09-15)

Reviewed: 566ca21..working tree — 42 files, +4605/-114 (18 tracked files modified,
24 new untracked files; scope confirmed with `git add -N .` / `git diff HEAD`
/ `git reset`, tree left exactly as found).
Reviewer context: fresh session, no prior context on this work item (cold review, as this project's deploy-skill requires).
Second opinion: none — a self-check (/ponytail-review + /simplify) ran during build; not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/H30-tether-enroll-frames-lifecycle: all present artifacts valid
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
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 95 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  4%]
.............................................
  two conversations, concurrently: 249 ms for two 200 ms turns
.  one conversation, twice: 450 ms for two 200 ms turns
.......................... [  9%]
........................................................................ [ 13%]
........................................................................ [ 18%]
........................................................................ [ 22%]
........................................................................ [ 27%]
........................................................................ [ 31%]
........................................................................ [ 36%]
........................................................................ [ 40%]
........................................................................ [ 45%]
........................................................................ [ 49%]
........................................................................ [ 54%]
........................................................................ [ 59%]
........................................................................ [ 63%]
........................................................................ [ 68%]
........................................................................ [ 72%]
........................................................................ [ 77%]
........................................................................ [ 81%]
........................................................................ [ 86%]
........................................................................ [ 90%]
........................................................................ [ 95%]
........................................................................ [ 99%]
.                                                                        [100%]
1585 passed in 116.56s (0:01:56)
TESTS OK
VERIFY OK
```

```
$ export PATH="$PWD/.venv/bin:$PATH"
$ timeout 150 python3 scripts/prove_tether_e2e.py
=== starting the fixture relay ===
=== minting an enrollment token ===
=== enrolling ===
hrn_prove
=== starting sadana gateway run ===
=== waiting for the box to connect ===
connected; hello capabilities: ['grammar.v1', 'changes', 'inventory', 'artifacts.download', 'approvals.wait', 'approvals.call', 'schedules.write', 'streaming', 'runs.live', 'runs.stop', 'plugins.install', 'plugins.inspect', 'plugins.write', 'upgrade']
=== forwarding GET /v1/harness ===
{'id': 'hrn_prove', 'version': '0.0.1', 'capabilities': [...], 'tether': 'connected', 'org': None, 'ledger_head': 4, 'leaves_the_box': [...], 'connected_at': '2026-09-15T09:27:48.547Z'}
=== forwarding GET /v1/conversations (a list) ===
{'data': [], 'next_page_token': None}
=== forwarding POST /v1/conversations (a create) ===
created conv_01a0a464dd0e7000ba71db4a718ba948
=== asserting the resulting event reached /events with a cursor ===
event log has 1 entries, cursors up to 5
=== killing the relay, simulating an outage ===
=== restarting the relay on the same port ===
=== waiting for reconnection (bound: 60s, full jitter) ===
reconnected after 0.4s
=== forwarding GET /v1/changes?since=0 after reconnect: nothing missing ===
changes catch-up includes the conversation created before the outage
=== isolated check: --retry-after is obeyed exactly ===
observed the retry-after log at 0.8s: 2026-09-15 09:27:50,823 sadana.tether tether: relay answered 429, sleeping exactly 7.0s (Retry-After)
ALL ASSERTIONS PASSED
```

Both runs done fresh in this session, not carried over from any prior claim.

## Findings

**Important.**

1. **[Security/Compliance] `.claude/settings.json`'s `Stop` hook — the one enforcing
   "make verify must be green before a session ends" — is deleted in this diff, with
   no mention anywhere in intent.md/spec.md/plan.md.** The diff replaces
   `{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-verify.sh\""}`
   with four blank lines inside the same `hooks` array, leaving `.claude/hooks/stop-verify.sh`
   itself untouched on disk but wired to nothing. `PreToolUse`'s chain-gate hook is
   untouched — only the stop-time verification gate is gone. This is entirely outside
   H30's own scope (enrollment/connection/lifecycle), is not named in plan.md's "Files
   that change", and removes exactly the safety net this project's own process depends
   on to catch a session that ends with a red tree. Whatever the intent, it should not
   ship silently inside an unrelated work item's diff.

2. **[Compliance] `CLAUDE.md` gained the "release tag byte-identical to
   `sadana.__version__`" rule, contradicting spec.md's own explicit instruction not to.**
   `spec.md` § "CLAUDE.md amendment — for approval, not applied" ends: "Not added to
   CLAUDE.md by this spec — surfaced for your approval first." The working tree's
   `CLAUDE.md` has that exact sentence added as a new `## Please do` bullet anyway
   (`CLAUDE.md`, new line after the loop-boundary-checkpoints bullet). This is not named
   in plan.md's file list either. Whether or not the rule itself is sound, applying a
   process-document change the spec itself flagged as needing the user's approval first,
   without recording that approval anywhere, is exactly the self-approval this project's
   own "Do not" list forbids.

3. **[Compliance] `src/sadana/client_surface.py` is touched, but appears nowhere in
   plan.md's "Files that change".** The one-line diff removes a duplicate
   `memory_context=memory_store.DispatchContext(account_key=account, conn=conn)` keyword
   argument from the `build_dispatch(...)` call inside `take_turn()`. Confirmed by
   `compile()`-ing HEAD's own copy of the file: it raises
   `SyntaxError: keyword argument repeated: memory_context` at that exact call — HEAD's
   `client_surface.py` cannot be imported at all as committed (566ca21 is a merge
   commit that evidently never had `make verify` run against the merged result). This
   diff's removal is very likely the right fix (it keeps the newer-shaped call with
   `conversation_key=conversation`, which matches `memory_store.DispatchContext`'s
   current fields) and something in this chain had to fix it for `make verify` to run
   at all — but it is a fix to a base-repo defect entirely unrelated to H30's own tether
   scope, and unlike `door/operations.py` and `plugin_install.py` (both explicitly
   called out in plan.md as "a closed file this item also touches, named here"),
   this edit is undisclosed anywhere in intent.md, spec.md, or plan.md.

4. **[Compliance] `scripts/upgrade.sh` implements the git-safety and service-restart
   design differently from what plan.md and spec.md describe, undisclosed.** Both
   `spec.md`'s Design section and `plan.md`'s own file list say the upgrade script uses
   `git -C <repo> checkout -- <tag>` (`--` before the console-supplied tag) and restarts
   the service "via `gateway_service.restart()`'s own existing `_run_systemctl` — reused,
   not re-implemented." The shipped script instead runs `git checkout "refs/tags/$tag"`
   (no `--`; the fixed `refs/tags/` prefix is a real, structurally equivalent defense —
   the argument git sees can never start with `-` regardless of `$tag`'s contents — but
   it is a different mechanism than the one named) and `sudo systemctl restart
   sadana-gateway` directly in bash, never calling into `gateway_service.restart()` or
   `_run_systemctl` at all. The direct `sudo systemctl` call is arguably the *necessary*
   choice — the gateway process runs unprivileged (per `docs/install.md`'s own
   NOPASSWD-sudoers step, added for exactly this), and `_run_systemctl`'s bare
   `systemctl` call has no path to a privileged restart without it — but plan.md never
   says so, and the service name `"sadana-gateway"` is now a second, independent literal
   alongside `gateway_unit.SERVICE_NAME`, free to drift. Both the `--`→`refs/tags/`
   substitution and the reuse→reimplementation change are real, security-relevant
   deviations from what was actually approved in plan.md, however sound the reasoning
   behind them looks in hindsight.

5. **[Bugs] `tether.client.stop()` cannot interrupt a connection that is still
   mid-handshake, so `deregister` can silently fail to stop the tether.**
   `_active_loop`/`_active_ws` (`src/sadana/tether/client.py:110-114`) are only set
   *after* `perform_handshake()` succeeds (`client.py:314-316`), and `stop()`
   (`client.py:117-138`) only closes something when both are non-`None`; when neither is
   set it only flips `_stop_event`, which is checked exclusively by `run_forever`'s outer
   `while` condition (`client.py:369`) — i.e. only between connection attempts, never
   during one. If `harness.deregister` (`door/nouns/harness.py::_deregister`) races a
   connection attempt that is between `websockets.connect()` and a completed handshake —
   plausible any time the box is reconnecting, which is the box's normal behavior after
   any network blip or a `429` — `stop()` does nothing to that attempt: it completes the
   handshake with the key pair already held in memory (the on-disk key file being deleted
   underneath it changes nothing already loaded), becomes fully `connected`, and keeps
   answering `request` frames and pushing ledger/ephemeral events indefinitely, since
   nothing inside a live connection's task group ever rechecks `_stop_event` either. The
   loop only actually stops the next time this connection drops on its own. This
   undermines Requirement 14's "the tether stops" for `deregister`, and Concerns' own
   discussion of when a box keeps dialing in doesn't cover this specific window — it only
   discusses the protocol's own inability to signal "revoked."

**Nits.**

- [Bugs] `tether.frames.door_response_to_response`'s `MAX_RESPONSE_BYTES` guard
  (`frames.py:280-303`) only applies on the base64/binary path; a large JSON response
  body has no size cap at all before being inlined into one WebSocket frame.
- [Compliance] `scripts/upgrade.sh` hardcodes the literal `"sadana-gateway"` service
  name a second time, alongside `gateway_unit.SERVICE_NAME` in Python — the two can now
  drift independently since a bash script can't import the Python constant.

## Decision

Approved by Adam, 2026-09-15.

Findings #1 (Stop hook removal) and #2 (CLAUDE.md tag/version amendment) were
already known and authorized earlier in this session, outside what the cold
reviewer could see from the diff alone: the user removed `.claude/settings.json`'s
Stop hook themselves, and the CLAUDE.md amendment is the one spec.md surfaced
for approval, which the user approved during Design.

Findings #3 (undisclosed `client_surface.py` fix), #4 (`scripts/upgrade.sh`'s
undisclosed deviation from plan.md's git-checkout/systemctl-restart design), #5
(the deregister/reconnect race), and both nits are approved to ship as-is and
are **not** fixed in this branch. They are real and stay open — tracked for a
follow-up Maintain-stage `intent.md`, not silently dropped.
