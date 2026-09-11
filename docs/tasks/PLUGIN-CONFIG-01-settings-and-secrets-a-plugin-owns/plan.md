# Plan: Settings and secrets a plugin owns (from intent.md 2026-09-11)

## Files that change

    src/sadana/plugins.py                    Setting, Manifest.settings, the two
                                             name patterns, setting_env_var,
                                             read_setting, missing_settings, the
                                             three new invalid-manifest outcomes,
                                             and the settings arm of
                                             manifest_to_dict / manifest_from_dict /
                                             manifest_to_toml / _parse_manifest
    src/sadana/plugin_manifest.py            settings + plugin-name checks inside
                                             check_manifest; the missing-value
                                             preflight at the top of run_graph;
                                             three lines in
                                             describe_manifest_outcome
    src/sadana/env_file.py         (new)     the .env write half, moved out of
                                             subcommands/setup.py unchanged
    src/sadana/subcommands/setup.py          imports those five helpers instead of
                                             defining them; no behaviour change
    src/sadana/subcommands/plugin.py         `plugin settings` and `plugin set`
                                             subcommands, plus the requirements
                                             line printed by `plugin install`
    tests/unit/test_plugins.py               Setting round trips, both name
                                             patterns, setting_env_var, the
                                             collision case, read_setting,
                                             missing_settings
    tests/unit/test_plugin_manifest.py       the three new validation outcomes
    tests/unit/test_plugin_manifest_run.py   the preflight: refused run, no node
                                             visited, empty trace, value absent
                                             from the result; and a two-node
                                             plugin whose second node reads the
                                             value once it is set
    tests/unit/test_env_file.py    (new)     upsert, drop, quoting, 0600 — moved
                                             from test_setup.py
    tests/unit/test_setup.py                 the moved tests removed; everything
                                             about `sadana setup`'s own behaviour
                                             stays and must keep passing untouched
    tests/unit/test_subcommands_plugin.py    `plugin settings` output, `plugin set
                                             --value`, the unknown-plugin and
                                             unknown-setting exits, and that
                                             nothing is written under the plugin's
                                             own directory
    src/sadana/editor_server.py    (added)   drops its own private copy of the
                                             plugin-name pattern and calls the
                                             shared one — see the amendment below
    src/sadana/plugin_install.py   (added)   checks the name at both doors it
                                             enters by, before it becomes a path
    tests/unit/test_plugin_install.py (added) the two regression tests for that

If `tests/unit/test_plugin_manifest_run.py` does not exist under that exact
name, the preflight tests go beside the existing `run_graph` tests wherever
they already live; `testing-conventions` puts them in the file named after the
module under test.

## Order of work

1. **`plugins.py`, the data and the naming.** Add `Setting`, add
   `settings: tuple[Setting, ...] = ()` as a trailing defaulted field on
   `Manifest`, add `PLUGIN_NAME_RE` / `SETTING_NAME_RE`, `setting_env_var`,
   `read_setting`, `missing_settings`, and the three new outcome dataclasses.
   Wire `settings` through all four serialization functions. Nothing calls any
   of it yet, so the suite must be exactly as green as it was before this step.
   Tests: `tests/unit/test_plugins.py`.

2. **`plugin_manifest.py`, validation.** Reject a plugin name outside
   `PLUGIN_NAME_RE`, a setting name outside `SETTING_NAME_RE`, and a duplicate
   setting name, each as its own outcome, each with a line in
   `describe_manifest_outcome`. This is the step that can break existing tests
   and fixtures, so it lands early and alone — a red suite here names its own
   cause. Every manifest name in the tree today (`memory`, `plugin-a` through
   `plugin-d`, `plugin-one`, `plugin-two`, `example-plugin`, `lonely`, `n`,
   `p`) already matches the pattern; if something else surfaces, it is a real
   finding and gets reported, not accommodated.
   Tests: `tests/unit/test_plugin_manifest.py`.

3. **`plugin_manifest.py`, the preflight.** At the top of `run_graph`, before
   the `resume` branch and before the first node, return a `DagResult` with
   `failed_node="entry"` when `missing_settings(manifest)` is non-empty. A
   manifest declaring no settings takes the same path it takes today, which is
   what every existing `run_graph` test proves.
   Tests: the preflight file named above.

4. **`env_file.py`, the move.** Lift `_env_path`, `_path_put`, `_env_line_key`,
   `_upsert_key`, `_drop_key` and `_quote_env_value` out of
   `subcommands/setup.py` into a new module, unchanged, public names without the
   leading underscore. `setup.py` imports them. This is a pure move and the
   proof it is one is that `sadana setup`'s own tests pass without being
   edited. It lands after steps 1–3 so that if the suite goes red here, the
   cause is unambiguous.
   Tests: `tests/unit/test_env_file.py` (moved), `tests/unit/test_setup.py`
   (reduced, not rewritten).

5. **`subcommands/plugin.py`, the human half.** `_render_settings(installed)`
   written once; `plugin settings <name>` and `plugin set <name> <setting>
   [--value V]` built on it; `cmd_plugin_install` prints the same rendering on
   success. Masked prompt via `getpass` when the setting is a secret, plain
   `input` when it is not; `--value` for the no-keyboard path.
   Tests: `tests/unit/test_subcommand_plugin.py`.

6. **The editor round trip, by hand.** Read `editor_assets`' save path and
   confirm the browser posts back the manifest object it was given rather than
   rebuilding it from known fields. If it rebuilds, the fix is to preserve the
   settings key; nothing here can be covered by a unit test, so this is a read
   and a manual run, reported as such.

7. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**What this could break, by name.** Four things already work and are in the
blast radius.

`sadana setup` is the largest: step 4 moves six functions it depends on. The
mitigation is that the move is textual and its existing tests are not allowed
to be edited — if they need editing, it was not a move and the step is wrong.

Every existing `run_graph` test goes through step 3's new early return. The
mitigation is the defaulted empty `settings` tuple: `missing_settings` on a
manifest that declares none returns an empty tuple and the preflight does
nothing. If a single existing run test changes behaviour, the default is wrong.

The visual editor and the marketplace both round-trip a manifest through
`manifest_to_dict`, and the marketplace stores that dict as JSON in SQLite. A
`settings` key that serializes but does not parse back — or parses back only
from TOML and not from a dict — silently deletes a plugin's settings on the
next save. Step 1's tests assert both directions of both round trips; step 6
covers the half that lives in JavaScript and cannot be tested here.

Every construction of `Manifest` in the tree, in `src/` and in tests, is
positional-or-keyword over five fields. A trailing defaulted sixth keeps all of
them compiling. The one thing that would not survive is a `Manifest` built by
positional argument *after* a new field inserted in the middle — so the field
goes last, deliberately, not next to `nodes` where it reads better.

**The most risky step is 2, and not for the obvious reason.** It is not risky
because validation is hard; it is risky because it *tightens* an existing
check, which means it can fail a plugin that passes today. Everything else in
this plan is additive and defaults to the current behaviour. The order already
puts it early rather than late, which is the opposite of the usual rule and is
deliberate: a tightening wants the loudest, earliest, most isolated failure it
can get, and burying it behind three other steps would mean reading a red suite
and guessing which change caused it.

**Where this plan could drift back into something the spec rejected.** Two
places, both plausible.

The first is the reserved `_sadana_settings` key. Step 5 will produce a moment
where a node body needs a value and calling `read_setting` with the plugin's
own name looks clumsy next to the `_sadana_memory_ctx` pattern sitting one file
over. `spec.md` § Rejected alternatives settles it and the reason is leakage,
not taste: a value in `arguments` can reach `DagResult.text`, a `NodeTrace`, and
whatever a recorded run persists. If the clumsiness is real it is a later
ergonomics work item, not a redesign inside this one.

The second is defaults. Step 1 will make `Setting` look like it obviously wants
a `default: str | None`, because a manifest field is free to add. It is not
free: a default has to be *injected* at run time, which means either mutating
the process environment or putting the value back into the data channel. The
spec's open question says required-only, widened later if the owner wants it.

**What has no mitigation.** Step 6. Nothing in this repository can test
JavaScript, so the editor round trip is confirmed by reading and by running it
once, and that is the weakest evidence in this work item.

## Proof

`tests/unit/test_plugins.py` covers: a manifest with no `[[setting]]`
round-tripping through TOML byte-identically to today — the dict form is *not*
byte-identical and this sentence originally claimed it was, corrected at
Deploy. `manifest_to_dict` now always emits `"settings": []`, which changed two
existing assertions in this file. `manifest_from_dict` tolerates the key being
absent, so marketplace rows stored before this change still load; a two-setting manifest surviving
`manifest_from_dict` → `manifest_to_dict` → `manifest_from_dict` and
`manifest_to_toml` → `_parse_manifest`; `setting_env_var("my-plugin",
"api_key") == "SADANA_PLUGIN__MY_PLUGIN__API_KEY"`; `setting_env_var("a-b",
"c") != setting_env_var("a", "b_c")`, which is the whole reason the separator
is doubled; `read_setting` returning `None` for unset and for empty;
`missing_settings` returning declaration order.

`tests/unit/test_plugin_manifest.py` covers each of the three new outcomes
separately — a plugin named `Foo`, a plugin named `../evil`, a setting named
`API KEY`, a setting declared twice — and that each has a distinct sentence
from `describe_manifest_outcome`.

The preflight tests cover: a plugin with one required setting unset returns
`failed_node == "entry"`, an empty `trace`, a `text` naming the setting, and a
body that records its own invocation is never invoked; the same plugin with the
variable set runs to its terminal node; a `call` body at the *second* node
reads the value through `read_setting`, which is the case the rejected
reserved-key design could not serve; and the resolved value appears in no
field of the returned `DagResult`.

`tests/unit/test_env_file.py` covers upsert-existing, upsert-new,
drop, the `KEY =` spacing normalization, newline stripping in a value, and mode
0600 on the created file — the same cases `test_setup.py` covers today, moved.
`tests/unit/test_setup.py` keeps every test about `sadana setup`'s own
resolution order and must pass with no edit; that is the proof step 4 was a
move.

`tests/unit/test_subcommand_plugin.py` covers: `plugin settings` printing one
line per declared setting with `set`/`not set` and never the value; `plugin
set --value` writing to the tmp state directory's `.env` and leaving the
plugin's directory byte-identical; exit 1 with nothing written for an unknown
plugin and for an undeclared setting.

Step 6's evidence is a sentence in the report naming what the editor's save
path does, plus a manual save-and-reload of a plugin that declares a setting.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

Three files were touched that this plan did not list, and one step turned out
smaller than planned. Recorded here rather than discovered in review.

**`src/sadana/editor_server.py` and the name pattern.** The plan had
`PLUGIN_NAME_RE` as new. It was not: `editor_server._SAFE_NAME` already
encoded the same rule, with its own comment citing the same CLAUDE.md line,
and the two disagreed — the editor's allowed a trailing and a doubled dash and
capped length at 64, the new one did neither. Two answers to "is this a valid
plugin name" depending which door you came through. Consolidated into one
pattern in `plugins.py` carrying both properties, `^[a-z0-9](-?[a-z0-9]){0,63}$`,
and `editor_server` now calls it. The 64-character cap is kept because the
editor's comment records why it is load-bearing: without it a long name passes
every check and then raises `OSError: File name too long` out of `handle()` as
a 500 carrying an absolute path.

**`src/sadana/plugin_install.py` — a live hole this plan's step 2 claimed to
close and did not.** `spec.md` asserts the check in `validate()` closes a
pre-existing hole. Tracing the callers shows `plugin_install.install()` never
calls `validate()` at all: `register()` accepts any string as a registry key,
`install()` computes `plugins_root / name` from it, and the write at the end of
that function is an `os.replace` over whatever is at that path. A registered
name of `../outside` places a fetched tree outside the plugins root and
clobbers what was there. Both doors now check, and `install()` re-checks rather
than trusting the table, because the row it reads is what decides where
`os.replace` writes. Two regression tests, written red first and confirmed red
for the right reason before either fix existed.

This is a scope expansion beyond the plan and beyond one commit's worth of
subject. It is taken here rather than deferred because this work item's own
`intent.md` constrains that "a name arriving from outside this project is not
trusted to be a sane one… checked before it is used to find anything", and
because shipping a `spec.md` that claims the hole is closed while it is open is
worse than a wider diff. A reviewer may reasonably want it split out; the two
tests and the `plugin_install.py` change are separable as a unit.

**Step 4 was smaller than planned.** The plan said to move a body of `.env`
helper tests out of `test_setup.py`. That file is `test_subcommands_setup.py`,
and it contained exactly one test that touched a helper directly — every other
test there goes through `cmd_setup`. So one test moved and
`tests/unit/test_env_file.py` was written as real unit tests for the
now-public module. `test_subcommands_setup.py` is otherwise unedited, which is
still the proof the move was a move.

**Step 6 found its bug in Python, not JavaScript.** The plan expected the
editor round trip to be at risk in the browser. `editor.js` never rebuilds the
manifest object — it posts back what the server gave it — so that half was
safe. `plugins.rename_node` and `plugins.remove_node` were not: both rebuilt a
`Manifest` field by field and silently dropped `settings`, so renaming or
deleting a step would have deleted a person's filled-in settings with nothing
failing until the next run. Fixed by the general change rather than the
specific one: both now use `dataclasses.replace()` on the manifest, the shape
`add_node` already had, so the next field added to `Manifest` cannot repeat
this.

**Two planned filenames did not exist.** `tests/unit/test_plugin_manifest_run.py`
(the plan anticipated this; the preflight tests went into
`test_plugin_manifest.py`), and `test_subcommand_plugin.py`, which is
`test_subcommands_plugin.py`.
