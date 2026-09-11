# Plan: Characters you write (from intent.md 2026-09-11)

## Files that change

    CLAUDE.md                                    the stable-prompt rule, approved at design
    src/sadana/persona.py                        rewritten (pure)
    src/sadana/persona_store.py                  (new, I/O)
    src/sadana/subcommands/persona.py            (new)
    src/sadana/cli.py                            +1 import, +1 builder call
    src/sadana/client_surface.py                 Runtime.persona deleted; _create resolves; take_turn slices
    src/sadana/conversation.py                   `Conversation.stable_prompt` + `rotate_prompt`'s prefix guard
    src/sadana/memory_store.py                   docstring cites the deleted `load_or_seed_persona`
    src/sadana/plugin_dispatch.py                `build_dispatch` loses its `stable_prompt` parameter
    src/sadana/scheduling.py                     docstring at :99 names Runtime's fields
    src/sadana/subcommands/gateway.py            comment at :68 names Runtime's fields
    tests/unit/test_persona.py                   rewritten (pure: parse/render/valid_name)
    tests/unit/test_persona_store.py             (new)
    tests/unit/test_subcommands_persona.py       (new)
    tests/unit/test_client_surface.py            + requirements 8 and 9; docstring at :32
    tests/unit/test_conversation.py              + `stable_prompt` and the two `rotate_prompt` guard cases
    tests/unit/test_plugin_dispatch.py           drops the `stable_prompt=` kwarg at 11 call sites
    tests/unit/test_subcommands_chat.py          persona-stability test ported to a character file
    tests/unit/test_subcommands_gateway.py       comment at :81
    tests/conftest.py                            make_runtime loses `persona=`; gains `write_character`
    scripts/prove_setup_fresh_instance.py        drops SADANA_CHAT_PERSONA_PATH (2 blocks, :230 and :281)
    scripts/eval/tasks/plugin_dispatch.py        drops the `stable_prompt=` kwarg (cold review)
    scripts/prove_conversation_e2e.py            drops the `stable_prompt=` kwarg (cold review)
    scripts/prove_plugin_dispatch_e2e.py         drops the `stable_prompt=` kwarg, 2 call sites (cold review)

Not touched, confirmed by reading: `eval_harness.py` builds its own
`TemplateRecipe` with no persona (`eval_harness.py:124`);
`conversation_store.py` needs no change at all, which is requirement 9's
whole point.

**This paragraph was wrong when written, and the cold review caught it.** It
originally said `build_dispatch()` keeps its signature and has one caller.
The step-4 amendment below removed the parameter, and `build_dispatch` has
five callers, not one: the four outside `src/` live in `scripts/`, which
`make verify` never touches (`pyproject.toml`'s `testpaths`, `mypy src`).
Those three files are listed above and fixed; a repo-wide AST check of every
`build_dispatch(` call site is in review.md § Evidence, because the failure
mode here is exactly one a green suite cannot see.

Amended during the build (step 4): `conversation.py` does change, by two
small additions the first draft of this plan did not anticipate. `CLAUDE.md`
was already modified before this stage began — the design stage's approved
rule amendment — and is listed above rather than left as an unexplained
working-tree change. `memory_store.py` and `subcommands/gateway.py` are
docstring and comment citations of things step 5 deletes; left stale they
would point at functions that no longer exist.

## Order of work

Each step leaves the suite green. The old `persona.py` functions stay alive
until step 5 so that no intermediate step breaks imports for the modules that
still call them.

1. **`persona.py` — the pure half.** Add `Character` (frozen: `name`,
   `description`, `tone`, `style`, `body`), `CharacterError`,
   `NEUTRAL_VOICE` (today's `_DEFAULT_PERSONA` text, promoted to a public
   constant that is never written to disk), `NEUTRAL_NAME = "none"`,
   `valid_name()` (`^[a-z0-9][a-z0-9_-]{0,63}$`, and `none` is not a valid
   character name), `parse(name, text)` and `render(character)`.
   `parse` splits `---` frontmatter the way `plugins._parse_skill_md`
   (`plugins.py:93-111`) does — deliberately a second copy, not an import; it
   raises `CharacterError` for a missing or unclosed delimiter, a missing or
   over-long `description`, or a character with no body, tone and style at
   all. `render` returns body, `Tone: …`, `Style: …`, empty parts dropped,
   joined by `\n` (adopted from `hermes_cli/personality.py:84-93`). Leave
   `load_or_seed_persona` and `persona_path_from_config` untouched.
   Rewrite `tests/unit/test_persona.py` around the new functions, keeping the
   two existing seed tests until step 5 deletes what they cover.

2. **`persona_store.py` — the I/O half.** `characters_dir_from_config()`
   (`config.env_path("SADANA_PERSONA_DIR", default=config.get_paths().config_dir / "characters")`);
   `character_path(dir, name)` — `valid_name()` first, then build, then
   `resolved.is_relative_to(dir.resolve())`, raising `CharacterError` at
   either gate; `load_character`, `list_characters` (sorted by name, skipping
   files that fail to parse rather than failing the listing),
   `write_template(dir, name)` (refuses an existing path); the
   `persona_selections` table (`account_key TEXT PRIMARY KEY`, `name TEXT NOT
   NULL`, `updated_at REAL NOT NULL`) with `ensure_schema`, `get_selection`,
   `set_selection`, `clear_selection` — modelled on
   `memory_store.get_rubric_override`/`set_rubric_override`
   (`memory_store.py:112-130`); and `resolve_voice(conn, account, dir)`,
   which is total: no selection, an unreadable file or a `CharacterError`
   all return `persona.NEUTRAL_VOICE`, and nothing propagates.
   `tests/unit/test_persona_store.py` covers each of those paths plus both
   containment gates.

3. **`subcommands/persona.py` and the CLI.** `list [account]`, `show <name>`,
   `use <account> <name>`, `new <name>`, using
   `subcommands/memory.py`'s `_open_schema_ensured_store()` scaffold with
   `persona_store.ensure_schema`. `use` resolves the character first and
   returns 1 with a message on stderr if it does not load; `use <account>
   none` clears. `list` with an account marks the selection, and marks it
   `(missing)` when the selected name no longer loads. `list` on an empty
   directory prints where characters live and that `persona new` makes one.
   Wire into `cli.py` beside the other builders. Add
   `tests/unit/test_subcommands_persona.py`. After this step the feature is
   usable end to end for selection, and no turn has changed yet.

4. **The one door.** In `client_surface.py`: delete the `persona` field from
   `Runtime` and its read in `open_runtime()`; add
   `persona_store.ensure_schema(conn)` next to `memory_store.ensure_schema(conn)`;
   in `_create()`, pass
   `stable_prompt=persona_store.resolve_voice(conn, account, persona_store.characters_dir_from_config())`;
   in `take_turn()`, stop passing a `stable_prompt` to `build_dispatch` at
   all — see the second amendment below.

   **Amended while implementing.** The plan said to `assert` at the slice
   that it is a prefix of `convo.system_prompt`, which spec.md § Concerns
   asked for. Writing it showed the assertion is vacuous: `s[:n]` is a prefix
   of `s` by definition, so it checks nothing. The real invariant — that
   `stable_prompt_len` still delimits the *voice* — is only violable by a
   writer of `system_prompt`, so the check moved to the writer: the slice
   became a `Conversation.stable_prompt` property (one place for the formula,
   the shape `pending_turn_key` already set), and `rotate_prompt` now raises
   `ValueError` if the new prompt does not still begin with it. That is
   CLAUDE.md's new rule made enforceable rather than stated, at its only
   after-creation call site, and it changes no behaviour today because
   compression never rotates (`context.py:76`).

   **Amended again, at the self-check.** The efficiency pass pointed out that
   once the conversation carries its own voice, `build_dispatch`'s
   `stable_prompt` parameter is derivable from its own first argument — and
   was being computed on every turn to serve the `ask` branch most turns
   never take. The parameter is gone; `plugin_dispatch.py:218` reads
   `conversation.stable_prompt` at the one place a child is built. That is
   CLAUDE.md's new rule made structural rather than observed: there is no
   longer a parameter through which a caller *could* hand a child some other
   conversation's voice. Eleven test call sites drop the keyword; behaviour is
   identical, since every one of them passed `""` from a recipe whose
   `stable_prompt` was already `""`. Update `conftest.make_runtime` (drop `persona=` and
   the paragraph explaining its absence), the docstrings at
   `client_surface.py:111`, `scheduling.py:99`, `test_client_surface.py:32`
   and the comment at `test_subcommands_gateway.py:81`. Add two tests to
   `test_client_surface.py`: a conversation created for an account with a
   selection has `system_prompt[:stable_prompt_len] == render(character)`,
   and a child turn inside a conversation is built from that same prefix
   after the selection has been changed underneath it.

5. **Delete the old persona.** Remove `load_or_seed_persona`,
   `persona_path_from_config`, `_DEFAULT_PERSONA` and every reference to
   `SADANA_CHAT_PERSONA_PATH`, including both blocks in
   `scripts/prove_setup_fresh_instance.py`. Port
   `test_subcommands_chat.py:162`'s
   `test_cmd_chat_resume_keeps_original_persona_after_a_later_edit` to write
   and select a character file, then edit that file — same conversation, same
   assertion, same strength. Drop the two seed tests in `test_persona.py`.

6. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, sort the findings into take-now / future-item / already-settled, act
   on the first bucket, then `make verify`.

## Risks

**What this could break that already works.** Four named things. (1) The turn
path itself: `client_surface` is the single door every client goes through,
so step 4 is reachable from `sadana chat`, the webhook channel, the scheduler
tick and every plugin child turn at once — `test_client_surface.py`,
`test_gateway_dispatch.py` and `test_scheduling.py` all drive turns through
`conftest.make_runtime`, which is why that fixture's signature change lands in
the same step rather than later. (2) `test_subcommands_chat.py:162` is an
existing test that already asserts exactly the behaviour this work item is
about — a conversation keeping its voice after the source text is edited. It
is not wrong and it is not being weakened; it is being pointed at the new
source of that text, and if it cannot be ported with its assertion intact
that is a signal the design is wrong, not the test. (3)
`scripts/prove_setup_fresh_instance.py` is outside `make verify` — mypy covers
`src` only and no test imports `scripts/` — so its edit is checked by running
it, not by the suite. (4) A developer with an existing store gets the new
table from `ensure_schema` on the next `open_runtime()`; a developer with an
existing `persona.md` silently stops being read, which is the intent's
accepted clean break and is worth saying out loud when this is reported.

**The riskiest step is 4, and it is fourth on purpose.** It is the only step
that changes a running path rather than adding an unreached one; steps 1-3
land a parser, a store and a CLI that nothing in the turn path calls yet, so
by the time the door is touched, `resolve_voice`'s fallbacks and the file
format are already proven by their own tests. The ordering was chosen for
exactly that reason: the alternative — change the door first, then build the
resolution behind it — has an intermediate state where every client is
resolving through code with no tests of its own.

**Where this plan could drift back into something the spec rejected.** Two
places, both in step 4. The first is keeping `Runtime.persona` "just for the
terminal" and resolving per account only in the gateway — that is the
rejected `open_runtime`-resolution alternative returning as a special case,
and it reintroduces the per-process copy of a per-account value. The second
is reaching for a new `persona` column on `conversations` the moment the slice
`system_prompt[:stable_prompt_len]` looks subtle; spec.md § Rejected
alternatives settled that the value cannot legally differ from the prefix
already stored, so a second copy buys a migration and a staleness path for
nothing. A third, smaller one sits in step 3: adding `rename` and `delete`
verbs because the four commands look asymmetric without them — they are a
declared non-goal, and `mv` and `rm` already work on a file the person owns.

**Known limitation, not mitigated here.** A conversation written before C12
added `stable_prompt_len` loads with that value defaulted to
`len(system_prompt)` (`conversation_store.py:335-336`), so step 4's slice
returns the whole prompt including the context tier for those rows. The
assertion in step 4 still holds (a prefix is still a prefix). This is
inherited rather than introduced — those conversations get a process-wide
persona today, which is not less wrong — and it self-corrects when the row is
next written. One consequence the self-check surfaced and this plan accepts:
for such a row the new `rotate_prompt` guard would reject *any* compressed
rotation, turning a silently over-wide prefix into a raised
`PromptDriftError`. Unreachable today, because compression never rotates
(`context.py:76`), and it belongs in front of whoever does the compression
work item rather than being defended against here.

## Proof

`tests/unit/test_persona.py` covers parse (valid, missing delimiter,
unclosed frontmatter, absent description, over-long description, a character
with nothing to say), render (all four field combinations, and that the name
and description never appear in the output) and `valid_name` (accepted forms,
uppercase, `/`, `..`, leading dash, over-length, and `none`).

`tests/unit/test_persona_store.py` covers `character_path` refusing a bad
name before building a path and refusing a symlinked escape after resolving
one; `list_characters` skipping an unparseable file instead of failing;
`write_template` refusing to overwrite; selection round-trip through an
in-memory connection; and `resolve_voice` returning `NEUTRAL_VOICE` for each
of no-selection, deleted-file and unparseable-file while raising nothing.

`tests/unit/test_subcommands_persona.py` covers each command's exit code and
output, including `use` on an unknown name exiting non-zero and leaving the
prior selection unchanged, `use … none` clearing it, and `list` marking a
selection whose file has since been deleted as missing.

`tests/unit/test_client_surface.py` gains three tests: a conversation created
in its account's selected voice, two accounts on one runtime getting their
own, and a child turn built from the voice its conversation started with
after the selection changed underneath it. `tests/unit/test_conversation.py`
gains the guard's own coverage — `stable_prompt` returns what the recipe put
in, a rotation preserving the prefix is accepted and bumps the epoch, and one
that rewrites it raises `PromptDriftError`. `tests/unit/test_subcommands_chat.py`'s ported test is the
end-to-end proof of the intent's hard constraint: create a conversation under
a selected character, edit that character's file, resume, and assert
`system_prompt` is byte-identical.

Then `make verify` ending `VERIFY OK`, pasted into the conversation, and
`scripts/prove_setup_fresh_instance.py` run by hand to show a fresh install
creates no persona file — that script being outside the suite by design.
