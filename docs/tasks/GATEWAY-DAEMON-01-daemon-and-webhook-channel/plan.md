# Plan: GATEWAY-DAEMON-01 — daemon and webhook channel (from intent.md 2026-09-08)

Design stage (`spec.md`) is complete: four hermes research passes, a full
Interface section, explicit Rejected Alternatives. This plan turns that
design into an implementation plan against the real current state of
`src/sadana/` — re-verified directly against `chat.py`, `plugin_dispatch.py`,
`conversation_store.py`, `conversation.py`, `config.py`, `cli.py`,
`test_subcommands_chat.py`, `conftest.py`, `scripts/prove_plugin_dispatch_e2e.py`,
and the specific hermes files spec.md names as adopted (`systemd_notify.py`,
`status.py`'s flock, `session.py::build_session_key`,
`platforms/base.py::handle_message`, and — since spec.md didn't cite one —
the actual stdlib `ThreadingHTTPServer` prior art, found in
`plugins/platforms/a2a/adapter.py`). That check surfaced four small,
necessary corrections to spec.md's own Interface pseudocode, called out
under Risks — none reopen anything in spec.md's Rejected Alternatives.

## Files that change

- `src/sadana/persona.py` (new) — `_DEFAULT_PERSONA`, `persona_path_from_config()`, `load_or_seed_persona()`, moved verbatim out of `chat.py`.
- `src/sadana/subcommands/chat.py` — drop the three moved names, import them from `sadana.persona` instead. No other behavior change.
- `tests/unit/test_persona.py` (new) — `test_load_or_seed_persona_creates_default_file` and `test_load_or_seed_persona_reads_existing_file_verbatim`, moved verbatim from `test_subcommands_chat.py`.
- `tests/unit/test_subcommands_chat.py` — remove the two moved tests; change its import of `load_or_seed_persona`/`persona_path_from_config` from `sadana.subcommands.chat` to `sadana.persona`.
- `src/sadana/gateway.py` (new) — `MessageEvent`, `session_key_for()`. Pure, no I/O, no import of `conversation.py`.
- `tests/unit/test_gateway.py` (new) — `session_key_for()` behavior.
- `src/sadana/gateway_dispatch.py` (new) — `handle_inbound()`.
- `tests/unit/test_gateway_dispatch.py` (new) — the continuity test (acceptance criterion 2).
- `src/sadana/channel_webhook.py` (new) — `WebhookUnauthorized`, `WebhookBadRequest`, `parse_webhook_request()`, `make_server()`.
- `tests/unit/test_channel_webhook.py` (new) — `parse_webhook_request()` only, no socket.
- `src/sadana/gateway_daemon.py` (new) — `run()`, `_notify_systemd()`, the flock helpers.
- `tests/unit/test_gateway_daemon.py` (new) — secret-unset refusal, flock double-acquire.
- `src/sadana/subcommands/gateway.py` (new) — `build_gateway_parser()`, `cmd_gateway_run()`.
- `src/sadana/cli.py` — one import line, one `build_gateway_parser(subparsers)` call inside `build_parser()`.
- `scripts/prove_gateway_webhook_e2e.py` (new) — the standalone real-socket/real-signal proof script, matching `scripts/prove_plugin_dispatch_e2e.py`'s shape. Not pytest-collected; its output is pasted into `review.md` at Deploy.

## Order of work

1. **`persona.py` extraction.** Pure move, zero new logic. Run `test_persona.py` and `test_subcommands_chat.py` narrowly; both must be green before anything else starts. Lowest-risk step, so it goes first.
2. **`gateway.py`** — `MessageEvent` (frozen dataclass: `platform: str`, `chat_id: str`, `thread_id: str | None`, `text: str`) and `session_key_for(event) -> str` (`f"webhook:{event.chat_id}"`, or with `:{event.thread_id}` appended when given). No dependencies on anything new. `test_gateway.py`: same `chat_id`/`thread_id` → same key; different `chat_id` → different key; `thread_id=None` omits the trailing segment.
3. **`gateway_dispatch.py`** — `handle_inbound()`, built directly on `plugin_dispatch.build_dispatch()` / `take_turn_and_reconcile()` and `conversation_store.load()` / `create()` / `bind_persist()` / `save()`, structured as a direct copy of `_chat_loop`'s per-turn body plus `cmd_chat`'s get-or-create branch — driven by catching `ConversationNotFound` instead of an explicit `--resume` flag:

   ```python
   async def handle_inbound(
       conn: sqlite3.Connection,
       event: MessageEvent,
       *,
       plugin_set: plugin_dispatch.PluginSet,
       persona: str,
       provider: str,
       model: str,
   ) -> tuple[bool, str]:
       key = session_key_for(event)
       now = time.monotonic()
       try:
           conversation = conversation_store.load(conn, key, now=now)
       except conversation_store.ConversationNotFound:
           template = ConversationTemplate(
               name="webhook",
               recipe=TemplateRecipe(stable_prompt=persona, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs),
           )
           conversation, _template = create_conversation(
               template, key, system_message="",
               iteration_budget=iteration_budget_from_config(),
               wall_clock_budget=wall_clock_budget_from_config(now),
           )
           conversation_store.create(conn, conversation, now=now)

       dispatch, tracker = plugin_dispatch.build_dispatch(
           conversation, plugin_set, stable_prompt=persona, provider=provider, model=model, now=now
       )
       persist = conversation_store.bind_persist(conn, conversation, now=now)
       result, conversation = await plugin_dispatch.take_turn_and_reconcile(
           conversation, dispatch, tracker,
           user_input=event.text, provider=provider, model=model, now=now, persist=persist,
       )
       await asyncio.to_thread(conversation_store.save, conn, conversation, now=now)

       text = result.final_text or f"[{result.exit_reason.value}] {result.detail or ''}"
       return result.exit_reason == ExitReason.COMPLETED, text
   ```

   **Return-type deviation from spec.md's Interface pseudocode** (`-> str`): changed to `-> tuple[bool, str]`. See Risks.

   `test_gateway_dispatch.py`: reuses `conftest.py`'s `monkeypatch.setattr(model_access, "send", lambda request: next(responses))` pattern exactly as `test_subcommands_chat.py` does. Acceptance criterion 2: call `handle_inbound()` twice against the same `conn`/`chat_id`, assert the stored conversation's `next_turn_seq` reaches `2`.

4. **`channel_webhook.py`** — `parse_webhook_request()` (pure, checked first with `hmac.compare_digest`, then JSON-parses and validates `chat_id`/`text`/optional `thread_id`) and `make_server()` (binds a real `ThreadingHTTPServer` subclass; `do_POST` calls `parse_webhook_request()`, then on a valid `MessageEvent` calls `on_message(event)` inline and writes `{"ok": ..., "text": ...}`; a stray exception from `on_message` is caught at this one boundary and turned into a `500`).

   **Critical correction versus copying hermes's A2A adapter verbatim**: hermes's `plugins/platforms/a2a/adapter.py` subclasses `ThreadingHTTPServer` with `daemon_threads = True`. **Do not copy that flag.** spec.md's own Lifecycle design relies on per-request handler threads being non-daemon (the stdlib default) so Python's interpreter-shutdown behavior — waiting for every non-daemon thread — is what lets an in-flight webhook request finish before the process exits, with no separate drain timeout built. Leave `daemon_threads` at its default.

   `test_channel_webhook.py`: `parse_webhook_request()` only — correct secret + well-formed body → `MessageEvent`; missing/wrong secret → `WebhookUnauthorized`; malformed JSON or missing `chat_id`/`text` → `WebhookBadRequest`. No socket involved, matching acceptance criterion 3.

5. **`gateway_daemon.py`** — the riskiest file (real `fcntl`, real `signal`, real threads), lands after everything under it is already proven. Structure:

   ```python
   def run(*, host: str, port: int, secret: str, on_message) -> int:
       if not secret:
           print("SADANA_GATEWAY_WEBHOOK_SECRET is not set; refusing to start", file=sys.stderr)
           return 1

       lock_path = config.get_paths().state_dir / "gateway.lock"
       lock_path.parent.mkdir(parents=True, exist_ok=True)
       lock_file = open(lock_path, "a+")
       try:
           fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
       except OSError:
           lock_file.close()
           print(f"another gateway instance already holds {lock_path}", file=sys.stderr)
           return 1

       server = channel_webhook.make_server(host, port, secret=secret, on_message=on_message)
       server_thread = threading.Thread(target=server.serve_forever, name="sadana-gateway-http")
       server_thread.start()
       _notify_systemd("READY=1")

       stop = threading.Event()
       signal.signal(signal.SIGTERM, lambda *_: stop.set())
       signal.signal(signal.SIGINT, lambda *_: stop.set())
       stop.wait()

       server.shutdown()
       server.server_close()
       server_thread.join()
       fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
       lock_file.close()
       return 0
   ```

   `_notify_systemd(message: str) -> bool`: `AF_UNIX`/`SOCK_DGRAM` write to `$NOTIFY_SOCKET`, handling the `@`-abstract-namespace prefix, silent `False` on unset/`OSError`/`UnicodeError`/`ValueError` — adopted close to verbatim from hermes's `gateway/systemd_notify.py:20-40`.

   Flock: non-blocking (`LOCK_EX | LOCK_NB`), adapted from hermes's `_try_acquire_file_lock` (`gateway/status.py:866-879`) — only the flock half, no PID file, matching spec.md's explicit choice.

   **`signal.signal()` must run on the main thread** — a hard Python constraint that directly shapes the standalone proof script's structure (step 7). hermes's own SIGTERM handling (`gateway/run.py`, `loop.add_signal_handler`) is asyncio-native and not directly reusable here — spec.md's stdlib `signal.signal()` + `threading.Event` shape has no hermes prior art; it's original code sized for `ThreadingHTTPServer`'s synchronous, thread-per-request model.

   `test_gateway_daemon.py`: `run(secret="")` → returns `1`, `channel_webhook.make_server` never called (assert via monkeypatch raising if called). Acquire the flock once, then a second `run()` call at the same lock path → returns `1` without calling `make_server`.

6. **`subcommands/gateway.py`** — thin glue, matching `cmd_chat`'s own wiring style:

   ```python
   def cmd_gateway_run(args: argparse.Namespace) -> int:
       host = args.host or config.env("SADANA_GATEWAY_HOST", "127.0.0.1")
       port = args.port or config.env_int("SADANA_GATEWAY_PORT", 8765)
       secret = config.env("SADANA_GATEWAY_WEBHOOK_SECRET", "")

       provider = config.env("SADANA_MODEL_ACCESS_PROVIDER", "openrouter")
       model = config.env("SADANA_MODEL_ACCESS_MODEL", "deepseek/deepseek-v4-flash-0731")
       persona = load_or_seed_persona(persona_path_from_config())
       plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
       conn = conversation_store.open_store(conversation_store.store_path_from_config())
       lock = threading.Lock()

       def on_message(event: MessageEvent) -> tuple[bool, str]:
           with lock:
               return asyncio.run(
                   gateway_dispatch.handle_inbound(
                       conn, event, plugin_set=plugin_set, persona=persona, provider=provider, model=model
                   )
               )

       return gateway_daemon.run(host=host, port=port, secret=secret, on_message=on_message)
   ```

   **This `threading.Lock()` is the load-bearing addition spec.md's Interface section doesn't show, and it stays.** `conversation_store.open_store()`'s own docstring states its connection is safe only "by one thread at a time, different call to call" — never touched by two threads *at once*. `ThreadingHTTPServer` gives each inbound connection its own thread, so without this lock, two concurrent webhook requests would call `handle_inbound()` on the same `sqlite3.Connection` from two threads simultaneously — exactly the condition CLAUDE.md's own `conversation_store.py` rule names as the trigger to revisit its single-connection design. The lock is the cheap alternative: it preserves the existing single-writer invariant with zero change to `conversation_store.py`, no second connection, no connection pool.

   `build_gateway_parser(subparsers)`: registers `gateway` with its own nested `run` subparser (`sadana gateway run [--host] [--port]`) — nested because that's literally the two-word command spec.md names, not speculative registry-building; only `run` is registered, no `stop`/`status` (not asked for; the process is stopped externally via signal).

   `cli.py`: add `from sadana.subcommands.gateway import build_gateway_parser` and one `build_gateway_parser(subparsers)` call in `build_parser()`. Verify `test_cli.py` still passes.

7. **`scripts/prove_gateway_webhook_e2e.py`** — last, since it's the only place all the real OS primitives run together. Structure:
   - Monkeypatches `model_access.send` at module level (plain reassignment, not pytest's fixture) to a canned response function — this script proves the daemon/socket/lifecycle path is real, not that `model_access`'s own provider round trip is real (already proven elsewhere).
   - Fixed local port (e.g. `18765`) and a fixed test secret, both plain module constants — no dynamic port discovery, matching `prove_plugin_dispatch_e2e.py`'s own preference for a simple, direct proof over general-purpose test infrastructure.
   - **Thread roles, corrected from a literal reading of spec.md's acceptance criterion**: `signal.signal()` can only be called from the main thread, so `gateway_daemon.run()` itself runs on the script's main thread (blocking, as it does in real deployment). A second, genuinely background thread drives the HTTP round trip: waits for the port to accept connections, POSTs twice against the same `chat_id` (asserting the second continues the first conversation, matching acceptance criterion 2's shape but over real HTTP), POSTs once with a wrong secret (asserts `401`), starts one more slow request (the canned `model_access.send` sleeps briefly) just before sending a real `os.kill(os.getpid(), signal.SIGTERM)` to the process, and asserts that slow request still completes with `200` rather than being cut off.
   - Guarded `if __name__ == "__main__":` entry point; plain `assert` statements with messages, printed step by step, matching `prove_plugin_dispatch_e2e.py`'s own shape exactly.
   - Output pasted into `review.md` under `## Evidence` at Deploy — the "first real external round trip" CLAUDE.md's rule requires; does not go in `make test`.

8. **Self-check and `make verify`.** Run `/ponytail-review` and `/simplify` against the full diff; apply anything in the "worth taking now" bucket in this same diff; note anything deferred in one sentence. Then run `make verify` and paste its output.

## Risks

**What could this change break?**
- `chat.py`'s existing tests — the persona-function move changes `chat.py`'s imports. Mitigated by running `test_subcommands_chat.py` narrowly right after step 1, before anything else builds on top of it.
- `cli.py`'s existing `test_cli.py` — adding a subcommand registration. Mitigated by running it narrowly after step 6.
- Nothing else in the existing codebase is touched; every other new file is net-new with zero existing callers, so blast radius elsewhere is zero.

**Most risky step, and why it's last:** `gateway_daemon.py` (step 5) and the standalone proof script (step 7). They're the only pieces exercising real OS primitives — `fcntl.flock` across process boundaries, real `SIGTERM`/`SIGINT` delivery, real thread lifecycles, a real bound socket — that pytest's isolated/mocked environment structurally cannot fully exercise (this is exactly why CLAUDE.md requires a standalone script for a block's first real external round trip rather than relaxing the unit suite's network ban). Both land only after `gateway.py`, `gateway_dispatch.py`, and `channel_webhook.py` are independently proven by direct unit tests, so if something breaks at step 5 or 7, the bridge logic underneath is already known-good.

**Four corrections to spec.md's own Interface pseudocode, found by reading the cited source files directly rather than trusting paraphrase — none reopen anything in spec.md's Rejected Alternatives:**

1. `gateway.py`'s `session_key_for()` is typed `-> str`, not `-> ConversationKey`. `ConversationKey = str` (`conversation.py:30`) is a plain alias, and spec.md's own prose (§Design, "Where the new code lives") is explicit that `gateway.py` has "no import of `conversation.py` or anything below it" — importing `ConversationKey` just for a type hint would violate that in letter even though `ConversationKey` and `str` are the same type. Returning the bare `str` type satisfies both the prose constraint and the interface's actual intent.
2. `handle_inbound()` (and therefore `on_message`) returns `tuple[bool, str]`, not `str`. spec.md's own wire contract needs `ok` to reflect `result.exit_reason == ExitReason.COMPLETED` (§"The webhook exchange itself"), but `_chat_loop`'s own comment (`chat.py:93-96`) notes `final_text` can be non-`None` even when `exit_reason != COMPLETED` (e.g. `BUDGET_EXHAUSTED` still carries a best-effort summary) — so `ok` cannot be reliably recovered by inspecting the returned text after the fact. Threading the bool through the return value is the direct fix; string-sniffing the reply to guess at `ok` is exactly the kind of "substring match against rendered content" CLAUDE.md already warns against in a different but analogous context (Task.grade()).
3. `channel_webhook.make_server()`'s `on_message` parameter type changes to match: `Callable[[MessageEvent], tuple[bool, str]]`.
4. `ThreadingHTTPServer`'s `daemon_threads` stays at its stdlib default (`False`) rather than copying hermes A2A's `daemon_threads = True` verbatim — see step 4 above. This is the paradigm's own "audit before copying" rule catching a real transplant hazard: hermes's flag choice fit hermes's own explicit-drain design; sadana's spec explicitly relies on the opposite (non-daemon threads) for its no-timeout drain story.

**Re-checked against spec.md's own Rejected Alternatives — no drift found:** no channel-adapter registry or discovery mechanism is built (direct call from `cmd_gateway_run` into `channel_webhook.make_server()`); no abstract `ChannelAdapter` base class exists; the webhook exchange stays synchronous request-in/reply-out (no background task, no separate delivery step); the daemon's default bind host stays `127.0.0.1`; persona loading is extracted rather than duplicated, matching the one rejected alternative that explicitly called for extraction.

## Proof

- `make test -k "test_persona or test_subcommands_chat"` green after step 1, before step 2 starts.
- `test_gateway.py`, `test_gateway_dispatch.py`, `test_channel_webhook.py`, `test_gateway_daemon.py` each green narrowly after their own step, covering every acceptance criterion in spec.md except the last two (the e2e script and `make verify` itself).
- `test_cli.py` green after step 6.
- `scripts/prove_gateway_webhook_e2e.py`'s full printed output — asserting `ok=true` on the first response, conversation continuity on the second (matching acceptance criterion 2's shape over real HTTP), `401` on a wrong secret, and a slow in-flight request completing after a real `SIGTERM` — pasted into `review.md` §Evidence at Deploy, per CLAUDE.md's standalone-script rule.
- `/ponytail-review` and `/simplify` run against the full diff, findings triaged (worth-taking-now applied in the same diff, future-work-item findings noted in one sentence, already-settled findings noted as spec.md holding).
- `make verify` output ending `VERIFY OK`, pasted in full when reporting the work done.

## Changed during planning

- Two research passes (this repo's own `subcommands/chat.py`/`plugin_dispatch.py`/`conversation_store.py`/`conversation.py`/`config.py`/`cli.py`/test-and-fixture conventions; and the specific hermes files spec.md names as adopted) ran in parallel before this plan was drafted, confirming exact signatures rather than trusting spec.md's own paraphrase.
- Four corrections to spec.md's Interface pseudocode surfaced from that direct verification (see Risks) — none change spec.md's Requirements, Design, or Rejected Alternatives; all are typing/signature-level fixes needed to make the interface as specified actually work.
- One gap in spec.md's own acceptance-criterion wording ("a real `gateway_daemon.run()` in a background thread") was found and resolved: `signal.signal()` is only callable from a process's main thread, so the proof script runs `gateway_daemon.run()` on its main thread and moves the HTTP-driving/SIGTERM-sending work to the background thread instead — same assertions, corrected thread roles.
