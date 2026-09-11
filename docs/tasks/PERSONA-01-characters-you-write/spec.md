# Spec: Characters you write

Intent: docs/tasks/PERSONA-01-characters-you-write/intent.md

## Requirements

1. **A character is a file the person owns.** One file per character at
   `<characters_dir>/<name>.md`; the filename stem is the character's name and
   the directory entry is the uniqueness constraint behind it. `characters_dir`
   is `config.get_paths().config_dir / "characters"`, overridable with
   `SADANA_PERSONA_DIR`. *(intent: "a document you write, keep in version
   control, copy between machines"; CLAUDE.md names-not-pointers.)*

2. **A character carries a description, a tone, a style and a voice.**
   `---`-delimited frontmatter with `description` (required, ≤ 1024 chars),
   `tone` and `style` (both optional); the body below the frontmatter is the
   voice prose. A character whose body, tone and style are all empty is
   invalid — it would say nothing. *(intent: "a name, a line describing it in
   their own words ... the tone it takes and the style it answers in".)*

3. **Rendering is fixed and total.** The prompt text of a character is its
   body, then `Tone: <tone>`, then `Style: <style>`, empty parts dropped,
   joined by newline. The name and description never reach the model.
   *(intent: the voice is what the assistant speaks in; the description is for
   the person reading a list.)*

4. **Four commands, one of which exists only so the files are findable.**
   `sadana persona list [account]` — every character's name and description,
   marking the account's selection when an account is given;
   `sadana persona show <name>` — the rendered voice;
   `sadana persona use <account> <name>` — select;
   `sadana persona new <name>` — write a template character file and print its
   path. *(intent: "no way to keep two voices and use one of them", "nothing
   tells you the file is there".)*

5. **`none` is the reserved name for the neutral voice.** `persona use
   <account> none` deselects. `none` is refused as a character filename.
   *(intent: "They can choose nothing, and get a plain, neutral voice".)*

6. **The selection is a name, stored per account, in the store.** A new
   `persona_selections` table, `account_key` primary key, holding the
   character's name. Rendered voice text is never written there.
   *(intent: "The character belongs to the person"; CLAUDE.md: a value holds
   another long-lived value's name, never a live reference.)*

7. **`use` fails loudly; resolution never fails.** `persona use` refuses a
   name that does not resolve to a readable, valid character file, and exits
   non-zero. Resolution during a turn — no selection, a deleted file, an
   unparseable file — falls back to the built-in neutral voice and raises
   nothing. *(intent: "anything a schedule started while they were asleep"
   must still answer.)*

8. **Every surface, without any client knowing.** Resolution happens inside
   `client_surface._create()`, so typed conversation, webhook replies,
   scheduled triggers and plugin child turns all speak the account's voice. No
   client, channel or subcommand resolves a persona of its own. *(intent:
   "all four must speak in the chosen voice, because the character belongs to
   the person and not to the doorway"; CLAUDE.md's one-door rule.)*

9. **A live conversation never changes voice, structurally.** The voice is
   baked into `Conversation.system_prompt` at creation and never re-read from
   process state. `plugin_dispatch.build_dispatch()` receives
   `convo.system_prompt[:convo.stable_prompt_len]` — the conversation's own
   stable prefix — instead of a process-wide persona string. *(intent's hard
   constraint; CLAUDE.md's byte-stable system prompt.)*

10. **Conversations recorded before this change keep loading.** One new table,
    no new column on `conversations`, no entry in
    `conversation_store._MIGRATED_COLUMNS`. *(intent: "anything recorded
    before this change is read back as it always was".)*

11. **A name is validated before it touches a path, and the path is
    re-checked after.** `^[a-z0-9][a-z0-9_-]{0,63}$`, then
    `resolved.is_relative_to(characters_dir.resolve())`. Both, never either.
    *(CLAUDE.md's filesystem-name rule.)*

12. **The old persona file stops existing.** `persona.md`,
    `SADANA_CHAT_PERSONA_PATH`, `load_or_seed_persona()`,
    `persona_path_from_config()` and `Runtime.persona` are removed; first run
    writes no persona file. *(intent: "it stops being read", "the first run no
    longer quietly writes a persona file on their behalf".)*

## Design

Two new modules and one rewritten one, a new table, and two lines of
`client_surface.py` that stop reaching for process-wide state. The character
itself is a file; nothing in sadana stores a second copy of what it says.

### Modules

- **`src/sadana/persona.py`** (rewritten, pure). `Character` frozen dataclass
  (`name`, `description`, `tone`, `style`, `body`); `NEUTRAL_VOICE` constant —
  today's `_DEFAULT_PERSONA` text, now a constant that is never written to
  disk; `parse(name, text) -> Character` raising `CharacterError`;
  `render(character) -> str` (requirement 3); `valid_name(name) -> bool`
  (requirement 11); `NEUTRAL_NAME = "none"`.
- **`src/sadana/persona_store.py`** (new, I/O). The characters directory and
  the selection table: `characters_dir_from_config()`, `character_path(dir,
  name)` (validate-then-contain), `load_character(dir, name)`,
  `list_characters(dir)`, `write_template(dir, name)`, `ensure_schema(conn)`,
  `get_selection(conn, account)`, `set_selection(conn, account, name, *,
  now)`, `clear_selection(conn, account)`, and the one call a turn makes:
  `resolve_voice(conn, account, dir) -> str`, which is total (requirement 7).
  Disk and SQLite in one module because both are I/O and CLAUDE.md's rule
  separates I/O from a block's *pure* module, not I/O from other I/O —
  `memory_store.py` already holds SQLite alongside a filesystem seed
  (`memory_store.py:132`).
- **`src/sadana/subcommands/persona.py`** (new). Parser and handlers in one
  file, the shape `subcommands/memory.py` already uses, including its
  `_open_schema_ensured_store()` scaffold. One line added to `cli.py`.

### Flow

`open_runtime()` loses its `persona` read entirely. `_create()` — the one
place a conversation is born — calls
`persona_store.resolve_voice(conn, account, dir)` and passes the result as
`TemplateRecipe.stable_prompt`. `take_turn()`'s `build_dispatch` call swaps
`runtime.persona` for `convo.system_prompt[:convo.stable_prompt_len]`.
`Runtime.persona` is deleted, and with it the process-lifetime copy of a
value that belongs to an account, not a process — which is what made the
gateway, one process serving many accounts, quietly wrong before this item.

Two disk-plus-SQLite reads per *conversation creation*; zero added to a turn.

### Policies applied

- **`testing-conventions`** — applied. New tests: `tests/unit/test_persona.py`
  (rewritten: parse, render, name validation — pure, no filesystem),
  `tests/unit/test_persona_store.py` (tmp-dir character files, in-memory
  connection, containment attempts, the total-resolution fallbacks),
  `tests/unit/test_subcommands_persona.py` (the four commands' exit codes and
  output), and additions to `tests/unit/test_client_surface.py` for
  requirements 8 and 9. Assertions are contracts, not snapshots: a
  conversation created for an account with no selection has
  `system_prompt[:stable_prompt_len] == persona.NEUTRAL_VOICE`, never a copy
  of that text spelled out in the test. `tests/conftest.py`'s `make_runtime`
  loses its `persona=` argument and the paragraph explaining it.
- **`project-structure` and `reference-lookup` do not exist** in
  `.claude/skills/`; their subject matter is covered by CLAUDE.md's layout
  rules and the reference section below. No security, brand or UX skill is
  present. Nothing else applies.

### Reference corpus

Read: `hermes_cli/personality.py` (186 lines), `hermes_cli/default_soul.py`
(112), and — to price the decline — `hermes_cli/colors.py` (38),
`hermes_cli/subcommands/skin.py` (30), `hermes_cli/tips.py` (512),
`hermes_cli/pets.py` (506), plus the `agent/pet/` tree (~2.5 kLOC including a
1183-line sprite atlas and a 251-line image-generation client).

**Adopted.** The rendering shape — body plus `Tone:`/`Style:` lines
(`personality.py:84-93`). The reserved neutral name
(`NEUTRAL_PERSONALITY_NAMES`, `personality.py:37`), narrowed from their four
spellings to one. And their hardest-won lesson, stated in their own module
docstring (`personality.py:15-25`): surfaces that each wrote persona state
differently — some the NAME, some the rendered TEXT — resurrected
personalities users had turned off, and needed a config migration to undo.
That is CLAUDE.md's names-not-pointers rule with a production incident behind
it; requirement 6 is that lesson, and requirement 8's single resolution point
is what makes a second divergent writer impossible rather than forbidden.

**Declined.** Their storage: user characters as a dict inside `config.yaml`
under `agent.personalities`, selection under `display.personality`. Two
specific things are wrong with it for us, not one preference — a character is
a document with prose in it and our config file is for behaviours (CLAUDE.md
splits those deliberately), and their own file-backed half (`SOUL.md`) exists
*in parallel* to the config half, which is exactly the two-stores-can-disagree
shape the intent forbids. We keep one store: the files. Also declined, per the
intent: their fifteen built-in characters, and every surface-side file above —
roughly six thousand lines that never reach the model.

### Guideline 2 — reducing bets

This work item sits *after* the plugin seam, so the narrow case applies: this
must not foreclose plugin-authored characters, and must not invent them. Three
bets, and the one that binds hardest is the file format. It is not a new bet:
`---` frontmatter plus a markdown body is already the format a user meets in
`SKILL.md` (`plugins.py:93-111`), so a character is a second instance of a
format this repo already teaches rather than a third format to learn. The
second bet — the name is the key — is the project's existing rule. The third —
selection in the store rather than in a file — is one table with one column
and one writer.

Future value arrives as an addition: more character files, written by a
person, with no core change. Nothing here forecloses a plugin writing one
later, because a plugin writing a character would write a file in the same
directory.

### Guideline 3 — which of the three moves

It makes an existing step heavier and adds none. Conversation creation already
composed a stable prompt from a value it was handed; it now resolves that
value from a name first. The turn path gets *lighter*: one field leaves
`Runtime`, and the value `build_dispatch` needs turns out to be already
present on the conversation. The cheaper alternative — resolve once at process
start, as today — cannot catch the scenario at all, because one gateway
process serves many accounts and the account is not known until the turn. The
weight this adds to every future caller of `_create` is two local reads on a
path that already writes a database row.

### Guideline 4 — state inventory

| State | Stored or derived | Why |
|---|---|---|
| Which character an account selected | **Stored** (one row, one name) | A person's choice. Nothing derives it. |
| A character's text, tone, style, description | **Stored by the user**, in their own file | Not our state; we only read it. |
| The rendered voice | Derived, per conversation creation | Composed from the character each time it is needed. |
| A conversation's own voice | Derived, `system_prompt[:stable_prompt_len]` | Already on the row. A new column would have been a second copy of a value that cannot legally differ from the first. |
| The list of characters | Derived, a directory scan | No index, no cache. |
| `Runtime.persona` | **Deleted** | A process-lifetime copy of a per-account value. |

Net: one table, one column of real state, minus one field and minus one file.

## Interface

**A character file** — `<characters_dir>/<name>.md`:

    ---
    description: how I want it to sound when I'm working
    tone: dry, unhurried
    style: short paragraphs, no lists unless asked
    ---
    You have read the code before answering. You say when you are unsure.

**`persona.py`** (pure): `Character`, `NEUTRAL_VOICE: str`,
`NEUTRAL_NAME: str`, `CharacterError(Exception)`,
`parse(name: str, text: str) -> Character` (raises `CharacterError`),
`render(c: Character) -> str`, `valid_name(name: str) -> bool`.

**`persona_store.py`** (I/O): `characters_dir_from_config() -> Path`;
`load_character(dir: Path, name: str) -> Character` (raises `CharacterError`
for a bad name, a missing file, non-UTF-8, or bad frontmatter);
`list_characters(dir: Path) -> tuple[Character, ...]` (skips unparseable
files rather than failing the listing); `write_template(dir, name) -> Path`
(refuses an existing file); `ensure_schema(conn)`; `get_selection(conn,
account) -> str | None`; `set_selection(conn, account, name, *, now)`;
`clear_selection(conn, account)`; `resolve_voice(conn, account, dir) -> str`
— total, never raises, `NEUTRAL_VOICE` on every failure path.

**CLI** — `sadana persona list [account] | show <name> | use <account>
<name> | new <name>`. Exit 0 on success; exit 1 with a message on `stderr`
for an unknown or invalid name in `show` and `use`, and for `new` over an
existing file. `list` on an empty directory prints one line saying where
characters go and that `persona new` makes one.

**Removed**: `persona.persona_path_from_config()`,
`persona.load_or_seed_persona()`, `client_surface.Runtime.persona`, the
`SADANA_CHAT_PERSONA_PATH` environment variable.

## Acceptance criteria

- [ ] A `.md` file dropped into the characters directory by hand appears in
      `sadana persona list` with its description, with no command run first.
- [ ] `sadana persona new mine` writes a file, prints its path, and refuses to
      overwrite it on a second run.
- [ ] `sadana persona use local mine` then a new `sadana chat` conversation:
      the conversation's stable prompt is exactly `render(mine)`.
- [ ] Editing `mine.md` afterwards does not change that already-started
      conversation's system prompt — byte for byte, across a reload.
- [ ] A child turn (a plugin `run` node) inside that conversation is built
      from the conversation's own stable prefix, not from whatever is
      currently selected.
- [ ] `sadana persona use local nope` exits non-zero and selects nothing.
- [ ] `sadana persona use local none` returns the account to the neutral
      voice.
- [ ] Deleting the selected character's file: the next new conversation gets
      the neutral voice, nothing raises, and `persona list <account>` shows
      the selection as missing.
- [ ] `sadana persona show ../../etc/passwd` and a name containing `/` are
      both refused by name validation before any path is built.
- [ ] A conversation row written before this change still loads and still
      answers.
- [ ] No persona file is created anywhere on a fresh install
      (`scripts/prove_setup_fresh_instance.py` no longer sets
      `SADANA_CHAT_PERSONA_PATH`).
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

Renaming and deleting characters through commands — they are files the person
owns, and `mv` and `rm` already work; a command for it would be a second way
to say the same thing. Per-conversation overrides and mid-conversation
switching. Sharing or installing characters from anywhere. Terminal colours,
skins, tips, goals, pets, achievements. Any shipped catalogue of characters.

## Open questions

Both carried from the intent, neither blocking. Whether characters should
become installable the way plugins are — nobody has asked yet, and the answer
changes nothing in this design because it would add a writer to the same
directory. And how far a character may reach: this design puts authored prose
into the system prompt with no filter, so a character that says "never ask
before running anything" is in the same text as the rest of the framing. The
line belongs to the user; see the first concern.

## Rejected alternatives

- **Characters in the config file, hermes's way** — declined above, with the
  two specific faults named rather than a preference.
- **Characters as rows in SQLite, commands the only door** — this was the
  simpler build (no parser, no path validation, no directory scan), and the
  intent explicitly bought files instead. It would also have made the
  version-control and hand-it-to-someone properties impossible rather than
  merely unimplemented.
- **Files plus a synchronised copy in the database** — the literal reading of
  "either way". Rejected as the thing the intent's own constraint forbids: two
  stores that can disagree. Files are the single store; commands are editors
  of files, not a second store.
- **A new `persona` column on `conversations`** — rejected by guideline 4. The
  value is already there as `system_prompt[:stable_prompt_len]`, and a second
  copy of a value that must never differ is a migration and a way to go stale
  bought for nothing.
- **Keeping `Runtime.persona`, resolved per account at `open_runtime`** —
  impossible for the gateway, which has no account at process start.
- **Reusing `plugins._parse_skill_md()`** — it is private, it raises
  `SkillLoadError`, and PLUGINS' leaf modules are deliberately kept from
  growing callers. Eight lines are duplicated instead, knowingly; a third copy
  is where a shared frontmatter helper earns its own module.
- **TOML for the character file**, matching `plugin.toml` — declined because a
  character is mostly prose and TOML makes multi-line prose an escape-sequence
  problem, while frontmatter-plus-body is already in the user's hands via
  `SKILL.md`.

## Concerns

**A character is unfiltered prose in the system prompt, and that is the thing
to look hardest at.** Requirement 3 puts authored text directly into the
stable prefix, ahead of everything else the conversation is framed by. A
character that instructs the agent to stop asking before it acts is not
distinguishable, at the text level, from one that asks for shorter sentences.
This design does not filter it, and the intent left the line unresolved. It is
tolerable today because the only author is the person running it on their own
machine; it stops being tolerable the moment a character can arrive from
somewhere else, which is precisely the first open question. Whoever answers
that question owns this.

**Two policies conflict over what happens when a character file goes bad, and
I followed one at a stated cost.** `config.py`'s posture is that a value set
but unparseable "should fail loudly at the one call site that reads it, not
silently fall back" — and CLAUDE.md requires that an unattended path never
raise into the run it is observing. A character file is both: a configured
value *and* something read at 3am by a scheduled trigger with nobody watching.
I split it by who is present — `use` fails loudly, resolution falls back
silently (requirement 7) — which is not a compromise that makes the tension go
away. The cost is real: a person who breaks their character file learns about
it only by running `persona list`, not from the next reply, which will be
fluent, neutral, and wrong.

**Requirement 9 rests on an invariant nothing currently checks.**
`system_prompt[:stable_prompt_len]` is exactly the stable prompt only while
every writer of `system_prompt` preserves that prefix. Today the only writer
after creation is `rotate_prompt`, driven solely by compression, and
compression never actually rotates — `context.py:76` keeps `new_system_prompt`
at `None`. So the slice is exact now, and would quietly start lying the day
compression begins rewriting the prompt without preserving byte 0 to
`stable_prompt_len`. The build stage should land an assertion at the slice,
not a comment.

**A legacy row makes the slice wider than the voice.** Conversations written
before C12 added `stable_prompt_len` load with it defaulted to
`len(system_prompt)` (`conversation_store.py:335-336`), so the slice returns
the whole prompt including the context tier. A child turn in such a
conversation gets a slightly over-wide stable prompt. This is inherited, not
introduced — today those conversations get `runtime.persona`, which is a
different wrongness, not a lesser one — and it is self-correcting on rewrite.
Flagged so a reviewer does not discover it as a surprise.

**`persona new` makes the CLI an author of the files it reads**, which is the
one place the single-store argument is thinner than it sounds. It stays
defensible only while `new` writes a template and nothing else ever writes
into that directory. A command that edited a character's fields in place would
be the beginning of the second store the intent forbids.

## Changed during design

Two things the interview had left as separate problems collapsed into
deletions once the code was read, and one requirement turned out to be already
built.

The intent's hard constraint — a conversation keeps the voice it started with
— needed no mechanism. `Conversation.system_prompt` already freezes the voice
at creation and `stable_prompt_len` already marks where it ends, so the
conversation has been carrying its own voice, retrievable byte-exact, since
C12. What the code was missing was the opposite of an addition: `build_dispatch`
reaches for a process-wide `runtime.persona` when the conversation's own
prefix is sitting on the row in front of it. The design removes that reach
rather than adding a column, which is why requirement 9 costs one expression.

"Files and commands must never disagree" was read during design as a
synchronisation problem and turned out to be a storage question. Once files
are the only store, there is nothing to synchronise and the constraint is
satisfied structurally rather than maintained.

`Runtime.persona` was expected to survive with a per-account lookup layered
over it. Reading `open_runtime()` showed it cannot: the gateway opens one
runtime per process and serves whichever accounts arrive, so a per-process
persona was already wrong for every surface except the terminal — an existing
defect this item removes as a side effect rather than a new capability.

The CLI lost a verb. `clear` was specified, then dropped in favour of
hermes's reserved neutral name, which was already being adopted for the
resolution path and now does both jobs.
