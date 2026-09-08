# CLI-SHELL block blueprint

Status: draft, revised 2026-09-08. Written against `hermes-agent@29112bef09`.
Cited by: CLI-SHELL-01 (`docs/tasks/`).

Revision note: the first draft assumed sadana runs the way it's developed —
one long-lived local WSL box, one person at it. Corrected in §1.1: the
deployed target is a fleet of individually-provisioned EC2 instances, one
per user, reached primarily through a messaging platform (not built yet)
and secondarily through VNC for ops. This flipped §4.2's gateway/daemon row
from declined to real-but-not-yet, and adjusted several others accordingly.

Design authority for the block that lets a person, or the agent itself,
actually reach the machinery every other block has built. Work items cite it
by section the way PLUGINS' work items cite `plugin_blueprint.md`.

This document exists because a work item (`CLI-SHELL-01-conversation-listing-
and-search`) was filed against this block before this block had been read.
The gap surfaced when asked directly: "have we included in scope everything
the CLI-SHELL block includes in hermes-agent?" — no. This document is what
answering that question properly requires: read the whole block first, then
decide what of it sadana needs.

---

## 1. Why this block exists

Every other block built so far — CONFIG, MODEL-ACCESS, CONVERSATION, PLUGINS
— is reachable only by importing it from Python. `conversation.py`'s
`take_turn()` runs an agent turn; `conversation_store.py` makes it durable;
`plugin_dispatch.py` runs a capability. None of that is invokable by a person
sitting at a terminal. CLI-SHELL is the block that turns "a library that
implements an agent" into "a program a person runs."

Concretely, today, `src/sadana/` has no `cli.py`, no `main.py`, no
`pyproject.toml` `[project.scripts]` entry, nothing `python -m` can reach.
Every block built in phase 1 has been proven only by its own test suite and
by hand-written proof scripts (`scripts/prove_conversation_e2e.py`,
`scripts/prove_plugin_dispatch_e2e.py`). CLI-SHELL is what replaces "a proof
script a maintainer runs by hand" with "a command."

### 1.1 The deployment target, corrected

A first draft of this document assumed sadana runs the way it's developed:
one long-lived WSL box, one person sitting at it. That assumption was wrong,
and it drove several conclusions below into a wall. Corrected, from the
maintainer directly:

- **WSL is where sadana-harness is built and tested, not where it runs.**
  CLAUDE.md's "WSL only" is a statement about this repo's dev/CI
  environment (`make`, `scripts/run_tests.sh`) — it says nothing about the
  deployed product's target OS. The real target is a plain Ubuntu EC2
  instance. This doesn't change any conclusion that declined something
  because it's *Windows*- or *Android*-specific (`win_pty_bridge.py`,
  `windows_ssh_runtime.py`, `psutil_android.py` — still irrelevant, EC2 runs
  neither); it changes every conclusion that declined something because
  sadana is "single-machine, one person at a local terminal."
- **The scale is one EC2 instance per user, provisioned and torn down
  automatically, at a fleet of thousands.** Not a shared multi-tenant
  server — each instance still has exactly one user, so §5's "no
  multi-tenant billing/accounts *within one instance*" still holds. What
  changes is that "single process, no daemon" was never really a
  constraint of *this block* — it was an artifact of nobody having a reason
  to reach the box remotely yet.
- **Fleet provisioning itself — spinning an instance up, tearing it down,
  assigning it to a user — is explicitly out of scope for sadana-harness.**
  A separate system owns that. This block's job is only to work correctly
  *once already running on a box*; it does not provision, register with, or
  manage a fleet.
- **Two real, different access modes exist for a running instance, per the
  maintainer:** end users normally reach their own instance's agent through
  a **messaging platform** (Telegram/Slack/etc. — not built yet, but the
  intended architecture); the maintainer and team reach a box for
  troubleshooting via **VNC** — a real remote desktop, i.e. a human sitting
  at an actual Ubuntu terminal, same as the dispatch shell in §4.1 already
  assumes. A browser-based dashboard was explicitly *not* named as part of
  the intended architecture — see §7 open questions before building one.

The practical effect: **§4.2's "gateway/daemon" row was wrong.** A
persistent, messaging-platform-facing daemon is the *primary* way an end
user reaches their instance, not a feature sadana's paradigm has no concept
of. It is corrected below, along with everything downstream of it.

## 2. What was actually read, and what it found

Five research passes covered hermes-agent's 88 CLI-SHELL production-code
files (per `docs/reference/hermes_core_blocks_kind.csv`, ~68,000 lines,
including a 22,268-line `cli.py` and a 14,834-line `hermes_cli/main.py`).
The findings below are cited to specific `path:line`s read during that
research; none of it is guessed from filenames — several filenames turned
out to be actively misleading (§4).

The headline finding: **hermes's own CLI-SHELL is not one coherent thing.**
It is a thin dispatch/wiring layer (§3) wrapped around five substantially
unrelated product surfaces that happen to share the `hermes_cli/` package —
a messaging-bot daemon, a multi-tenant billing UI, a browser dashboard's REST
API, two unrelated things both called "proxy," and the actual command-line
agent shell. Copying the package structure would copy the collision, not the
design.

## 3. What already exists that this block must not duplicate

sadana has, today: `config.py` (env/config resolution — the one place a
"which config loader am I inside" question has an answer), `conversation.py`
+ `conversation_store.py` (a conversation that survives the process,
sqlite-backed, `create`/`save`/`load`/`search_conversations`), and a plugin
system (`plugins.py`, `plugin_manifest.py`, `plugin_dispatch.py` — every
agent capability is reached through a plugin DAG, per
`docs/reference/plugin_blueprint.md`). CLI-SHELL's job is to expose these,
never to reimplement a second path to what they already do. A subcommand
that wants the agent to do something calls `plugin_dispatch`; it does not
grow its own capability logic, the same way hermes's own subcommand handlers
are consistently one-to-three-line dispatchers that import a same-named
implementation module (`hermes_cli/main.py:5941-5944`, `:3510-3515`,
confirmed across every subcommand read).

## 4. What hermes's CLI-SHELL actually is, once separated by what it touches

### 4.1 The dispatch shell (the part CLI-SHELL is actually about)

`pyproject.toml`'s `hermes = "hermes_cli.main:main"` entry point
(`hermes_cli/main.py:13152`) builds one `argparse.ArgumentParser` with
`add_subparsers()` (`hermes_cli/_parser.py:143`), parses, and calls
`args.func(args)` (`hermes_cli/main.py:14826`). `hermes_cli/main.py`
(argv → subcommand dispatch) and `cli.py` (the interactive REPL engine,
`class HermesCLI(CLIAgentSetupMixin, CLICommandsMixin, CLIBillingMixin)` at
`cli.py:5183`) are two separate modules joined by a plain in-process function
call (`cmd_chat` → `from cli import main as cli_main; cli_main(**kwargs)`,
`hermes_cli/main.py:3491-3493`) — not a subprocess. That split ("parse argv
and pick a command" vs. "run an interactive session") is worth keeping
deliberately from day one.

**The load-bearing, generic pieces** — the things any CLI shell needs,
regardless of product:

- An argparse tree with subparsers, nested where a command family needs it
  (`hermes_cli/main.py:14098`'s `sessions` sub-subparsers).
- Curated help text beyond argparse's default (`RawDescriptionHelpFormatter`
  + a hand-written epilogue, `hermes_cli/_parser.py:97-133,146-147`).
- Exit codes as the actual contract: a handler's return value becomes the
  process exit code (`hermes_cli/main.py:14820-14828`). **Pick this
  convention once** — hermes itself has two competing ones even inside one
  file (some handlers `return status`, others call `sys.exit()` internally;
  `cmd_approvals` at `hermes_cli/main.py:5917-5923` does both, leaving dead
  code after its own `sys.exit`). This is a concrete, avoidable mistake, not
  a style preference.
- Consistent stderr-routed error formatting (every handler read prints
  errors to `sys.stderr`, though the exact prefix/glyph varies —
  `hermes_cli/main.py:5667` vs `:5949` — another place to pick one shape
  and hold it).
- `--version`, checked before any heavier work
  (`hermes_cli/main.py:14754-14756`).
- No shared context object passed to handlers. Every `cmd_*` function takes
  only `args: argparse.Namespace` and does its own import, inside the
  function body, of whatever module it actually needs
  (`hermes_cli/main.py:5941-5944` and consistently elsewhere). This keeps a
  subcommand's own import graph exactly as wide as its own logic needs —
  worth adopting deliberately rather than inventing a `CliContext` object
  no requirement here asks for (guideline: minimise mutable state, don't
  store what a function can just import).

**The mistake not to repeat**: `hermes_cli/main.py` was, and largely still
is, a "god-file" being decomposed live — every subcommand module already
carved out of it carries a header stating it was "extracted verbatim from
`hermes_cli/main.py:main()` (god-file Phase 2)"
(`hermes_cli/subcommands/logs.py:3`, `doctor.py:3`, `model.py:3`, a dozen
more), and the ones *not yet* extracted are inline in a 14.8k-line function.
The extraction's own indirection — a subcommand module builds only the
`argparse` parser and receives its handler as an injected callable, because
the real handler still lives in `main.py` and importing it directly would
create a cycle (`hermes_cli/subcommands/__init__.py:10-13`, states this
outright) — is a solution to a problem sadana does not have. **A sadana
subcommand module owns both its parser and its handler from the first line
of code that exists for it.** One file per subcommand, no god-function to
carve out of later, no dependency-injection dance to dodge a cycle that
never gets created. `subcommands/pause.py` is hermes's own evidence this
works: it's the one subcommand that already defines its own handler and
imports its target block directly (`agent.estop`, `subcommands/pause.py:19,
38`) — the "graduated" shape, not the transitional one.

**A second, unrelated dispatch surface lives in the same package and must
not be conflated with the one above**: `commands.py` + `cli_commands_mixin.
py` implement an in-*chat* `/slash` command registry (`COMMAND_REGISTRY`,
`commands.py:87,143`) consumed by the CLI REPL, the messaging gateway,
Telegram, Discord, and Slack alike — one declarative table projected onto
five different surfaces (`commands.py:1-9,668,719,1604`). This exists
because hermes has five surfaces to serve from one registry. **Sadana has
one surface** (a person at a terminal, or the agent's own future callers) —
there is no gateway, no Telegram bot, nothing else this registry pattern
would be amortising its cost across. Building an equivalent multi-surface
slash-command registry now would be exactly the "invent a plugin system
before a second plugin exists" mistake `plugin_blueprint.md` §2 warns
against, aimed at a different subsystem. If sadana ever needs in-chat
commands, argparse's own subcommand tree already covers it for one surface;
revisit only if a second real consumer of the same command *table* (not
just the same *concept*) shows up.

### 4.2 Everything else — separated by what it actually is

Confirmed by reading, not by filename (several names were actively
misleading — `runtime_provider.py` is provider/credential resolution, not a
runtime/OS check; `route_identity.py` is URL cache-invalidation, not
session/process identity; `platforms.py`/`platform_actions.py` are
chat-messaging-platform registries — Telegram/Discord/Slack — not
OS-platform code; `windows_ssh_runtime.py` is a native-Windows Desktop-app
process-lifecycle/ACL helper, unrelated to SSH):

| Group | What it is | Applies to sadana? |
| --- | --- | --- |
| Gateway/daemon, the messaging-bridge half (`gateway.py`'s systemd lifecycle at `:4479-4905`, `subcommands/gateway.py`) | Installing/supervising a persistent OS-service daemon that bridges to a messaging platform + a cron scheduler. `gateway.py` itself never touches conversation/session code — it's pure process lifecycle (PID guard, restart-on-crash, platform setup wizard); the actual message-relay logic lives one block over in `GATEWAY-DAEMON`, per-platform adapters in `CHANNELS`. | **Real, not yet.** Per §1.1 this is the intended primary access path for end users. EC2 Ubuntu has real `systemd` (unlike WSL), so `gateway.py`'s systemd half (`systemctl` unit generation, install/start/stop/restart/status, "is another instance already running" guard) is a directly applicable precedent — its launchd (macOS) and Windows-Scheduled-Task halves aren't. CLI-SHELL's own job here is narrow: the `hermes gateway ...` lifecycle *verb*. The actual platform bridge is a future `GATEWAY-DAEMON`/`CHANNELS` block's work, not this one's — not scheduled until that exists (see §6). |
| Gateway/daemon, the cloud-relay-enrollment half (`gateway_enroll.py`) | A self-hosted gateway pairs with a hosted control-plane connector so the connector can route/wake it — a fleet-provisioning-shaped concern. | **No**, and for a sharper reason than before: fleet provisioning is explicitly out of scope for sadana-harness (§1.1) — a future control plane's own enrollment step, if it ever needs one, is that system's design to make, not something to build speculatively here. |
| First-run/setup on a fresh instance (hermes's onboarding flow, e.g. `cli_agent_setup_mixin.py`'s `_offer_first_run_setup`, `init_command.py`'s pattern) | A one-time in-instance configuration step run once a box exists — credentials, identity, initial config. | **Real, not yet.** The maintainer named "deploy a machine dedicated to a new user with an automated workflow" as a real future need. Fleet provisioning (triggering that deploy) is out of scope here, but the in-instance step it would invoke once — a `hermes setup`/first-run command — is exactly this block's kind of work. Not scheduled until a provisioning workflow exists to call it (see §6). |
| Long-lived-process memory hygiene (`mem_trim.py`) | Rate-limited heap release for "long-lived Hermes gateway processes." | **Real, once the messaging-bridge daemon above exists** — same reasoning hermes states for it. Not relevant to a short CLI invocation; relevant to a persistent per-instance daemon. Not scheduled until that daemon exists. |
| Two unrelated "proxy" concepts (`proxy/*`, `proxy_cli.py`/`hermes egress`) | `hermes proxy`: inbound, local, OAuth-credential-*sharing* reverse proxy so third-party apps ride the user's login. `hermes egress`: outbound, TLS-intercepting, credential-*hiding* egress firewall for untrusted sandboxed sub-agents (wraps a third-party binary, iron-proxy). hermes's own source has a comment flagging that it collided on the word "proxy" for two opposite things (`proxy_cli.py:11-14`). | **No**, and a naming lesson either way: no multi-app credential sharing scenario is named, and the untrusted-sandboxed-execution scenario `hermes egress` answers is EXECUTION/SAFETY's question (`plugin_blueprint.md` §2.3), not CLI-SHELL's, if it ever arrives — worth another look once thousands of instances are running arbitrary plugin code, but that's that block's open question, not a reason to build it here. |
| Billing/accounts (`cli_billing_mixin.py`, 1,566L) | Multi-tenant org billing UI: subscriptions, cards, charges, admin roles. | **No.** Sadana has no product, no billing, one user — this is CLAUDE.md's stated single-user posture, not a gap. |
| Multi-profile identity (`subcommands/profile.py`, `profile_describer.py`) | Managing several named agent identities and auto-describing them for an orchestrator's routing UI. | **No.** One install, one identity. |
| Windows/Android-native shims (`win_pty_bridge.py`, `windows_ssh_runtime.py`, `psutil_android.py`, Windows branches of `stdio.py`/`clipboard.py`) | Native-platform compatibility code (pywinpty, pywin32 ACLs, a Termux `psutil` source patch). | **No.** The deployed target is Ubuntu on EC2 (§1.1) — not Windows, not Android, regardless of what OS development happens on. |
| pty/terminal bridging + web dashboard (`pty_session.py`, `pty_bridge.py`, `web_routers/*`, `web_git.py`, `web_models.py`) | Confirmed (only non-test callers: `web_server.py`, `web_routers/sessions.py`) to be one bundle: a loopback-bound browser dashboard, with the pty bridge letting a browser tab attach to a real pty of hermes's *own* TUI over a WebSocket. **Not** CLI-REPL infrastructure. | **Not named as the intended access model** (§1.1: messaging platform for end users, VNC for ops) — a real remote-desktop session over VNC already gives the maintainer's team an actual terminal, which is exactly what §4.1's dispatch shell already serves. Revisit only if a browser-based interface is later chosen over VNC — see §7. |
| Legacy migration tools (`claw.py`, `agent_import.py`, `image_provenance.py`) | One-off: import a competitor's ("OpenClaw") settings; import Claude Code/Codex config into hermes; detect an immutable Docker image to refuse self-update-in-place. | **No.** No competitor migration, no self-update-in-place mechanism exists or is planned. |
| Cron-dependent commands (`blueprint_cmd.py`, `suggestions_cmd.py`, `send_cmd.py`) | Automation blueprints, proactive suggestions, and message-sending, all wired to hermes's cron scheduler and gateway credentials. | **No.** No cron subsystem, no messaging platforms. |
| Middleware contract (`middleware.py`) | A request/tool-call interception chain letting a plugin rewrite a tool or model call before it runs. Misfiled here by hermes (it's used throughout the ordinary agent loop, not gateway-specific). | **Not CLI-SHELL's.** If sadana's plugin system ever needs "a plugin can rewrite a call before it runs," that's a PLUGINS-block open question (see `plugin_blueprint.md` §12), not this block's. |
| Provider/credential resolution (`runtime_provider.py`, `route_identity.py`) | "Which provider/model/key is active" resolution, and cache-invalidation when a cached derived value (like a context-window pin) should be distrusted because config drifted. Misfiled here by hermes; both are MODEL-ACCESS concerns merely reused by the gateway and CLI alike. | **Not CLI-SHELL's.** Sadana already has `model_access.py`; if multi-provider fallback or drift-invalidation is ever needed, that's a MODEL-ACCESS work item. |
| "Project" workspace concept (`projects_cmd.py`, `projects_db.py`) | A per-profile sqlite store for a human-named workspace anchored to one primary repo path — used to group desktop sessions by folder and to name kanban worktrees/branches deterministically. hermes's own docstring calls this a "footprint-ladder rung-2... zero model-tool schema cost" capability — i.e. optional even there. | **Not now.** The underlying idea (a long-lived thing gets a name, not a path, per CLAUDE.md's own naming rule) is worth remembering if sadana ever spawns worktrees for background work, but nothing today anchors it to anything. |
| Generic-*shaped* commands already covered elsewhere (`init_command.py` → `/init`, `loops.py` → `/loop`) | Both are, in hermes, exactly what they sound like: `/init` seeds the live agent to write its own `AGENTS.md`/`CLAUDE.md`; `/loop` is recurring in-session wakeups, fixed-interval or self-paced. | **Already exist** as sadana-harness's own Claude Code built-in skills (see this file's own CLAUDE.md cheat sheet). Not a CLI-SHELL gap — a coincidence worth noting, not a to-do. |
| Genuinely-later polish (`completion.py`, `agent/lsp/cli.py`, WSL branch of `clipboard.py`, `pt_input_extras.py`, `debug.py`/`dump.py`-shaped diagnostics, `worktree_cmd.py`) | Shell-completion generation from a live argparse tree (zero dependency, pure polish); a substantial standalone LSP-integration for post-write diagnostics; clipboard image paste via `powershell.exe` from WSL; `prompt_toolkit` keyboard-protocol patches (only relevant if sadana ever adopts `prompt_toolkit`); a support-bundle "about" command; git-worktree hygiene (relevant only once sadana's agent spawns worktrees). | **Plausible, not required.** None blocks anything; none has a consumer yet either. Build if and when a real need names one. |
| Plugin-registered CLI subcommands (`plugins/google_meet/`, `plugins/teams_pipeline/`) | Two product-specific plugins that happen to register their own CLI verbs — via **two different, uncoordinated mechanisms** even within hermes itself (`google_meet` calls a raw `register_cli(subparser)`; `teams_pipeline` calls `ctx.register_cli_command(...)` on the plugin context). Not CLI-SHELL content at all — miscategorized/colocated files. | **Not CLI-SHELL's.** If a plugin ever needs to add a CLI verb, that's a PLUGINS-block extension-point decision — and hermes's own two incompatible answers are a reason to decide it deliberately once, not copy either. |

## 5. What this block must satisfy

1. **No second path to capability.** A subcommand that wants the agent to
   act calls the existing plugin/dispatch machinery
   (`plugin_dispatch.py`/`conversation.py`); it never grows its own copy of
   logic another block already owns. (§3, and `plugin_blueprint.md` §3.4's
   "one shape all the way down," transplanted.)
2. **One file, one subcommand, from the start.** No god-function to
   decompose later, no injected-callable indirection to dodge a cycle that
   was never created. (§4.1.)
3. **One exit-code convention, one error-formatting shape**, decided before
   the second subcommand exists, not discovered as drift afterward. (§4.1.)
4. **No context object invented to pass state around.** A handler imports
   what it needs, when it needs it — matches how every other sadana module
   already resolves its own dependencies, and there's nothing here yet that
   needs sharing. (§4.1; revisit only if a real cross-subcommand state need
   appears — see §7 open questions.)
5. **Ubuntu on EC2, one user per instance, no billing, no accounts, no
   multi-tenancy within one instance.** These still hold (§1.1) — one
   instance never serves more than one user's billing/identity/accounts,
   regardless of how many instances exist across the fleet. What does
   *not* hold any more: "no daemon." A persistent per-instance process is
   the real, named future access path for end users (§1.1, §4.2) — not a
   drifted-from-brief mistake if one gets built, so long as it's this
   block's narrow lifecycle-verb slice and the actual bridge logic stays
   in a future `GATEWAY-DAEMON`/`CHANNELS` block (§3's "no second path").
   Fleet provisioning itself (spinning an instance up at all) stays
   entirely out of this repo's scope regardless.
6. **Config resolution goes through `config.py`, once**, never a
   subcommand-local reimplementation of "how do I find the config file"
   (CLAUDE.md: "know which config loader you are inside").

## 6. Work items

Sized against this block's own now-understood shape, and against this
project's own precedent (PLUGINS ran nine items in two halves). Small,
single-commit slices; order matters because each depends on the last having
something real to call.

| # | Item | Depends on | Closes |
| --- | --- | --- | --- |
| 1 | `search_conversations()` in `conversation_store.py` | CONV-08 (done) | A saved conversation can be found without its exact name — store-side only, no CLI wiring, driven by a test. Already scoped; see `docs/tasks/CLI-SHELL-01-conversation-listing-and-search/`. |
| 2 | The bare dispatch shell | nothing new | One `argparse` entry point, `--version`, the exit-code/error-formatting convention picked once (§5.3), zero subcommands yet beyond what's needed to prove the shell runs and exits correctly. |
| 3 | `hermes sessions list` / `hermes sessions search <query>` | items 1, 2 | Item 1's store function gets its first real caller — this is "session listing and search finally have a caller," the exact deferred half named when this work started. Thin: argv → `search_conversations()` → printed rows. |
| 4 | `hermes chat` (or equivalent default command) | item 2, `conversation.py`/`conversation_store.py` (done) | The actual interactive agent loop, wired to `create`/`load`/`run_turn`/`bind_persist`. This is CLI-SHELL's real center of gravity and its own work item's spec.md will need its own design pass — not scoped further here. |
| 5 | `hermes gateway` lifecycle verb | item 2; a future `GATEWAY-DAEMON`/`CHANNELS` block | Install/start/stop/status/restart for a persistent per-instance daemon, systemd-only (§4.2). **Not scheduled** — nothing to bridge to yet; real, not declined. |
| 6 | `hermes setup` / first-run | item 2 | The in-instance step an (out-of-scope) provisioning workflow would invoke once per new instance (§4.2). **Not scheduled** — no provisioning workflow exists yet to call it; real, not declined. |
| — | Everything in §4.2's "No" rows (cloud-relay enrollment, both "proxy" concepts, billing, multi-profile, browser dashboard, Windows/Android shims, legacy migration, cron-dependent commands, middleware/provider-resolution misfiles, plugin-registered CLI verbs) | — | Not scheduled, and not a backlog either — declined because sadana's paradigm names no scenario for them, not because they're merely low-priority. |

Item 1 is already filed and its own `intent.md`/`spec.md` stand on their own
merits (§3's "no second path" and §5's one-user-per-instance/no-billing
constraints both already held for it, by construction — it added a query
function, not a CLI). This blueprint changes nothing about item 1's design; it changes *why*
item 1 is item 1: it is the store-side prerequisite item 3 needs, not an
isolated guess at CLI-SHELL's scope.

## 7. Open questions

1. **Does `hermes sessions` need anything beyond list/search** (item 3) —
   export, prune, repair, the way hermes's own `sessions` sub-subparsers do
   (`hermes_cli/main.py:14098`, ~20 actions)? Nothing in sadana's current
   shape asks for those yet; decide when item 3 is actually specced, against
   a real requirement, not this document's guess.
2. **Does a handler ever need shared state a fresh import can't give it**
   (a resolved config object read once per process, say)? §5.4 says no
   context object for now; if item 4's `chat` command finds a real need,
   that item's own spec.md is where to decide it, weighed against the
   "minimise mutable state" guideline.
3. **If sadana's plugin system ever wants a plugin to register its own CLI
   verb**, hermes's two incompatible answers (§4.2) mean there's no single
   pattern to adopt — this is a PLUGINS-block question to raise there, not
   here, when a real plugin needs it.
4. **Middleware-style call interception and provider/credential fallback**
   (§4.2's `middleware.py`, `runtime_provider.py` rows) are real ideas with
   no home in this document — they belong to PLUGINS and MODEL-ACCESS
   respectively, flagged there rather than decided here.
5. **Does sadana-harness build the messaging-platform bridge itself**
   (a future `GATEWAY-DAEMON`/`CHANNELS`-equivalent), or is that also a
   separate system's job the way fleet provisioning is? §1.1 only
   confirmed the *access model* (messaging platform, named by the
   maintainer); it didn't confirm *who builds it*. Decide before item 5
   (§6) is ever specced — if it's out of scope the way provisioning is,
   item 5 shrinks to nothing to bridge to, indefinitely.
6. **Is VNC assumed sufficient for ops/troubleshooting access
   indefinitely, or does a browser dashboard eventually get built
   alongside it** (§4.2's pty-bridge/web-dashboard row)? Not named as
   needed today; worth deciding deliberately once it comes up rather than
   building it speculatively now.
