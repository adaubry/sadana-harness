# Spec: Settings and secrets a plugin owns

Intent: docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns/intent.md

## Requirements

Each traces to a paragraph of `intent.md`.

1. **A plugin declares what it needs.** `plugin.toml` gains a repeated
   `[[setting]]` table: `name`, `purpose`, and `secret` (a boolean).
   A manifest that declares none is exactly what a manifest is today.
   *(intent: "A plugin can state, as part of itself, the values it needs".)*
2. **A declaration that cannot work is refused before anything runs.**
   `validate()` rejects a duplicate setting name, a setting name outside the
   allowed pattern, and — new, and wider than this work item's own need — a
   *plugin* name outside the allowed pattern.
   *(intent: "a new declaration is a new way to be wrong".)*
3. **A declared value is addressed by a name nobody else can collide with.**
   One environment variable per declared setting, named
   `SADANA_PLUGIN__<PLUGIN>__<SETTING>`, both halves uppercased, hyphens in the
   plugin name becoming underscores, the two halves separated by a *double*
   underscore that neither half may contain.
   *(intent: "The values are keyed by the plugin".)*
4. **A required value that is not set stops the run before the first step,**
   with a message naming every missing setting and the command that fills it
   in. Nothing in the message is the raw value of anything.
   *(intent: "the run stops with a message that names the value".)*
5. **A running step reads its plugin's values through one function,**
   `plugins.read_setting(plugin, name)`, which is the only place the naming
   rule in requirement 3 is written down.
   *(intent: "the steps that need those values have them".)*
6. **The model cannot see, supply, or override a value.** Values never enter
   the run's data channel: not `arguments`, not a node's threaded value, not
   `DagResult.text`, not `trace`, not a recorded run.
   *(intent: "A secret never appears in what the model reads or writes".)*
7. **A person can see what a plugin needs and fill it in.**
   `sadana plugin settings <name>` lists every declared setting, its purpose,
   whether it is a secret, and whether it is currently set — never its value.
   `sadana plugin set <name> <setting>` stores one, prompting without echo when
   the setting is a secret. `sadana plugin install` prints the same list on
   success.
   *(intent: "told about it at install time rather than at first failure".)*
8. **Values are stored where this project already stores secrets** —
   `state_dir/.env`, the file `config.load_dotenv()` already reads and
   `sadana setup` already writes — and never inside the plugin's own
   directory.
   *(intent: "Secrets go where this project already keeps secrets… Neither
   ends up inside the plugin's own files".)*
9. **A manifest carrying settings survives every round trip it already makes:**
   `manifest_from_dict` / `manifest_to_dict` / `manifest_to_toml` /
   `_parse_manifest`, and therefore the visual editor's save and the
   marketplace's stored manifest JSON.
   *(intent: "every existing plugin has to keep working".)*

## Design

The shape is a declaration in the manifest, a namespaced environment variable
per declared value, one function that reads it, and one preflight that refuses
to start a run that cannot work. The central decision — and the one worth
arguing with — is that a value never travels in the run's own data channel.

### Where the code lives

Three modules change; no new module is created.

**`src/sadana/plugins.py`** — the static contract, and already the module that
resolves `SADANA_PLUGINS_DIR` from the environment, so an environment read
here is the shape that is already there rather than a new one.

- `@dataclass(frozen=True) class Setting: name: str; purpose: str; secret: bool`
- `Manifest` gains `settings: tuple[Setting, ...] = ()` — a defaulted trailing
  field, so every existing construction of a `Manifest` (tests, the editor's
  `add_node`/`rename_node`/`remove_node` `replace()` calls) keeps compiling and
  keeps meaning what it meant.
- `PLUGIN_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")` and
  `SETTING_NAME_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")`.
- `setting_env_var(plugin, name) -> str` — pure. The one place requirement 3's
  rule is written.
- `read_setting(plugin, name) -> str | None` — `os.environ.get` of the above.
- `missing_settings(manifest) -> tuple[str, ...]` — the declared settings whose
  variable is unset or empty, in declaration order.
- `manifest_to_dict` / `manifest_from_dict` / `manifest_to_toml` /
  `_parse_manifest` each gain the `settings` arm.

**`src/sadana/plugin_manifest.py`**

- `check_manifest()` gains three outcomes — `InvalidPluginName`,
  `InvalidSettingName`, `DuplicateSettingName` — each a frozen dataclass in
  `plugins.py` beside the existing eight, each with a line in
  `describe_manifest_outcome`.
- `run_graph()` gains a preflight, before the first node and before any
  `resume` bookkeeping: if `missing_settings(manifest)` is non-empty, return a
  `DagResult` with `failed_node="entry"` and a text naming the missing settings
  and the command that sets them. This is the single door every execution path
  already goes through — `dispatch`, the eval harness, and the gateway's resume
  — so it is one placement rather than three.

**`src/sadana/subcommands/plugin.py`** — two new subcommands and one added line
in `cmd_plugin_install`, in the file that already owns `plugin register` and
`plugin install`. `_render_settings(installed)` is written once and used by all
three. Writing a value reuses `setup.py`'s existing `.env` upsert, which is
lifted out of `setup.py` into a shared helper rather than written twice; that
is a move, not a rewrite, and `setup.py`'s own behaviour does not change.

### Why the value never travels in the data channel

The obvious design is CLAUDE.md's own reserved-key rule: merge the values into
`arguments` under `_sadana_settings`, the way `_sadana_memory_ctx` and
`_sadana_session_key` already arrive. It is rejected, for two reasons.

**It does not reach far enough.** `run_graph` threads exactly one value: the
entry's `arguments` for the first node, each node's own output after that
(CLAUDE.md, "A plugin graph's node receives only its immediate predecessor's
output"). A key merged into `arguments` is visible to the *first* node only. A
plugin shaped `compute` (build the query) → `call` (fetch it) — the common
shape — would have to hand its own API key forward through the compute step's
return value to reach the step that uses it. That makes the secret part of the
run's data, which is the next problem.

**It puts the secret into the data channel.** Anything in the threaded value can
end up in a node's output, in `DagResult.text`, in a `NodeTrace.detail`, and in
whatever `record_plugin_run` persists. Keeping the values out of that channel
entirely means requirement 6 needs no redaction step anywhere, now or later: a
secret cannot leak into a record it was never in. An ambient read is the
weaker mechanism in the abstract and the stronger one here, because the thing
being carried must not be carried.

The reserved-key rule is not contradicted — it governs *execution identity*
that the model might otherwise supply, and its point is that the model must not
control it. A value the model cannot name, reach, or influence at all satisfies
that point more completely than a key it could at least collide with.

### Which of the three moves this makes

The uncaught scenario is "a plugin needs a credential and there is nowhere to
put one." The design **makes one existing step heavier** and **adds one step**.

The heavier step is manifest validation, which already walks every entry and
node and now also walks the settings and checks the plugin's own name. It is
the cheapest possible placement: install, the marketplace's vetting, the
editor's save and `discover_plugins` all already route through it, so one
check catches the scenario at all four without any of them knowing about it.

The added step is the preflight in `run_graph`. There is no existing step that
could absorb it: `dispatch` is not the only caller, install cannot know what
will be unset at run time, and a node body discovering the gap itself is
exactly the failure the intent describes. It costs every future run one
dictionary lookup per declared setting, and a plugin declaring none pays
nothing.

The alternative move — **make a step harder** — would be to gate a plugin's
entries out of the tool surface when its settings are unset, which is what the
reference does. Rejected below.

### Policies applied

- **`testing-conventions`** — every new test is a unit test against a tmp home;
  `monkeypatch.setenv` on the namespaced variables is the real mechanism, not a
  fake environment; no test reads source text; assertions are about the
  relationship between a declaration and its resolved variable, never a
  snapshot of which settings some fixture happens to declare.
- **CLAUDE.md, "Use .env for secrets / config for behaviours"** — partly
  followed, and the conflict is in `## Concerns`.
- **CLAUDE.md, "A caller-supplied name that becomes a filesystem path is
  checked against an allowlist pattern"** — requirement 2's plugin-name check
  applies this rule at load time.

  **Corrected at Deploy, 2026-09-11.** This paragraph originally claimed the
  check "closes a hole that predates this work item: a plugin name already
  becomes `plugins_root / name` and `.../skills/<skill>` with no pattern check
  anywhere in the tree." The cold review falsified all three parts and the
  original sentence is left quoted here rather than quietly rewritten, because
  what was believed at design time is the thing this chain exists to record.
  (a) A pattern check did exist — `editor_server._SAFE_NAME` — and disagreed
  with the new one. (b) The check in `validate()` does not close the install
  hole, because `plugin_install.install()` never calls `validate()`; what
  closes it is `plugins.plugin_dir()` at the install site and the `register()`
  guard, both added during Build and recorded in `plan.md` § Amended during
  implementation. (c) The `.../skills/<skill>` half is still unchecked:
  `plugins._skill_path` builds a path from `ref.skill` and `_check_skill` only
  tests that the file exists. That last one is pre-existing, out of scope here,
  and is the first line of the maintenance `intent.md` this work item owes.
- **CLAUDE.md, "A registry or dispatch seam… earns its cost only once a second
  real member exists"** — no settings-backend seam; one resolution path.
- **CLAUDE.md, "the system prompt is byte-stable for the life of a
  conversation"** — the tool surface does not depend on whether a setting is
  set. See the rejected alternative.
- **CLAUDE.md, "A plugin's outcome crosses back to its caller only as a
  returned DagResult"** — the preflight returns one; it raises nothing.
- **`project-structure`, `reference-lookup`** and any security or brand skill:
  not present in `.claude/skills/`. `reference-lookup`'s job was done by hand
  against `hermes_core_blocks_kind.csv` and the live corpus, recorded below.

### What the reference does, and what is taken from it

hermes solved this and the shape is worth copying. `plugin.yaml` carries
`requires_env` (39 of 101 manifests) and `optional_env` (21), and an entry is
either a bare string or a table of `name` / `description` / `prompt` / `url` /
`password` (`plugins/platforms/sms/plugin.yaml`,
`plugins/platforms/irc/plugin.yaml`, `plugins/video_gen/fal/plugin.yaml`).
`hermes_cli/config.py` surfaces them in its config UI;
`tools/registry.py:1243` aggregates them per toolset and
`check_tool_availability` marks a toolset unavailable when they are unset.

**Adopted:** the declaration itself, and the per-entry secret flag —
hermes's `password: true` is our `secret`, and it exists for the same reason:
a prompt that must not echo.

**Declined, with the reason:**

- **Two shapes for one field.** `requires_env` is a list of strings in some
  manifests and a list of tables in others, so every consumer branches on the
  shape. One shape here.
- **`prompt` alongside `description`.** In every hermes manifest read, the two
  are the same sentence twice. One field, `purpose`.
- **`url`.** hermes's own `web/exa` manifest shows why it is not enough:
  the signup URL is in the *description* prose ("Requires EXA_API_KEY — sign up
  at https://exa.ai") rather than the structured field, because prose is where
  an author naturally writes it. A per-setting `purpose` is the structured slot
  that absorbs it; a second optional field for the same sentence is a bet with
  no return.
- **A global, unprefixed variable name.** `FAL_KEY`, `EMAIL_PASSWORD`,
  `IRC_SERVER` — two plugins wanting `API_KEY` collide, and nothing can tell a
  plugin reading its own key from one reading another's. hermes pays for this
  visibly: `tools/plugin_guard.py` must *exempt* "reads an env secret" from its
  third-party plugin scanner because every legitimate plugin does it
  (`plugin_guard.py:14-26, 69`), which costs it the ability to flag the
  illegitimate case. A namespaced variable does not remove the ability of
  in-process code to read anything — see `## Concerns` — but it does make the
  legitimate access pattern distinguishable from every other one, which is the
  property hermes gave up.
- **Gating the tool surface on whether the value is set.** Rejected below.
- **`optional_env` as a separate list.** Not adopted as a second list; see
  Open questions.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| a plugin's declaration | stored, in `plugin.toml` | it is part of what the plugin *is*; it ships with the code it describes and versions with it |
| a setting's value | stored, in `state_dir/.env` | it is the one thing here that cannot be derived: a person typed it |
| the variable name for a setting | derived, every call | a pure function of two names, so it can never disagree with itself |
| which settings are missing | derived, every run | reading it fresh is what makes "set it, run again" work without restarting anything |
| whether a setting is set, for display | derived | never cached, never stored, never the value itself |

Nothing new is stored in a database, nothing is cached, and nothing gains a
lifetime. The one stored thing that did not exist before is a line in a file
that already exists and already has a reader and a writer.

## Interface

**`plugin.toml`, new and optional:**

```toml
[[setting]]
name = "api_key"
purpose = "Account key for the weather service — sign up at example.com"
secret = true

[[setting]]
name = "units"
purpose = "metric or imperial"
secret = false
```

**`plugins.py`:**

- `Setting(name: str, purpose: str, secret: bool)` — frozen.
- `Manifest.settings: tuple[Setting, ...] = ()`.
- `setting_env_var(plugin: str, name: str) -> str`. Pure. Asserts both names
  match their pattern — a caller that did not validate is a bug here, not a
  lookup that quietly returns nothing.
- `read_setting(plugin: str, name: str) -> str | None`. `None` when unset or
  empty. This is what a node body calls.
- `missing_settings(manifest: Manifest) -> tuple[str, ...]`. Setting names, in
  declaration order.

**`plugin_manifest.py`:**

- `validate()` / `check_manifest()` may now return `InvalidPluginName(name)`,
  `InvalidSettingName(setting)`, or `DuplicateSettingName(name)`, each with a
  sentence in `describe_manifest_outcome`.
- `run_graph()` may now return, before any node runs,
  `DagResult(plugin=…, entry=…, text="<plugin> needs a value for 'api_key',
  'units'. Set each with: sadana plugin set <plugin> <setting>",
  failed_node="entry")`.

**CLI:**

- `sadana plugin settings <name>` — one line per setting: name, `secret` or
  `setting`, `set` or `NOT SET`, purpose. Exit 0. Exit 1 if the plugin is not
  installed. Exit 0 with one line saying so if it declares none.
- `sadana plugin set <name> <setting> [--value V]` — `--value` for the
  no-keyboard path; otherwise prompt, masked when `secret` is true. Exit 1 and
  write nothing when the plugin or the setting is not declared.
- `sadana plugin install …` — on success, additionally prints what the newly
  installed plugin needs, marking what is already set.

**Errors:** every one of these is a returned value or an exit code. Nothing
here raises across a boundary, and no message anywhere contains a setting's
value.

## Acceptance criteria

- [ ] A `plugin.toml` with no `[[setting]]` loads, validates and runs exactly
      as it does on `main`, with no file changed.
- [ ] A `plugin.toml` with two settings round-trips through
      `manifest_from_dict` → `manifest_to_dict` → `manifest_from_dict`
      unchanged, and through `manifest_to_toml` → `_parse_manifest` unchanged.
- [ ] Saving a manifest in the visual editor preserves its settings.
- [ ] `setting_env_var("my-plugin", "api_key")` is
      `"SADANA_PLUGIN__MY_PLUGIN__API_KEY"`.
- [ ] `setting_env_var("a-b", "c")` and `setting_env_var("a", "b_c")` differ.
- [ ] A duplicate setting name fails validation; a setting named `API KEY`,
      `-x`, or `` fails validation; a plugin named `../evil` or `Foo` fails
      validation.
- [ ] `run_graph` on a plugin with a required-but-unset setting returns a
      `DagResult` whose `failed_node` is `"entry"`, whose `text` names the
      setting, whose `trace` is empty, and which visited no node — proved by a
      body that records that it ran.
- [ ] With the variable set, the same plugin runs, and a `call` body two nodes
      deep reads the value via `read_setting`.
- [ ] The resolved value appears nowhere in the returned `DagResult` — not in
      `text`, not in any `NodeTrace.detail`.
- [ ] `sadana plugin settings` on a plugin with a secret set prints `set` and
      does not print the value.
- [ ] `sadana plugin set --value` writes to `state_dir/.env` and nothing under
      the plugin's own directory is modified.
- [ ] `sadana setup` behaves exactly as before, proved by its existing tests
      passing untouched.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Editing or removing a value from the CLI. `state_dir/.env` is a text file and
  `sadana plugin set` overwrites; a delete verb waits for someone to want it.
- A different set of values per end user. That belongs to `AccountKey`, the
  identity that outlives a conversation, and is explicitly out of the intent.
- Encrypting anything at rest, rotation, or expiry.
- Drawing settings in the visual editor. The editor must not *destroy* them;
  it need not yet author them.
- Any change to how `sadana setup` collects its own two fixed values.
- A settings facility for anything that is not a plugin.

## Open questions

**Should a setting be able to carry a default value?** Not in this work item:
a setting is required, full stop, and a plugin wanting optional behaviour reads
`read_setting` and handles `None` itself. A `default` in the manifest would
mean the runtime has to *inject* a value, which means either mutating the
process environment or reintroducing the data channel this design removed.
Widening `required = false` later is additive; narrowing is not. The owner may
disagree and it is cheap to reverse.

**Should `sadana setup` learn about plugin settings?** No, and the reason is
sequencing rather than taste: `setup` is the first-run command and runs before
any plugin is installed, so it has nothing to ask about. If the owner wants one
command that fills every gap at once, that is a later, separate verb over both.

## Rejected alternatives

**A reserved `_sadana_settings` key merged into `arguments`.** The mechanism
CLAUDE.md already names, and the first thing tried. Rejected on reach and on
leakage — argued in full in `## Design`. It is the alternative a reviewer
should push back on hardest.

**Gating a plugin's entries out of the tool surface when a setting is unset**,
which is what hermes does (`registry.check_tool_availability`). It makes a step
*harder* rather than adding one, and it looks free. It is not. The tool surface
is in the system prompt, and CLAUDE.md holds the system prompt byte-stable for
the life of a conversation; a surface that depends on environment state makes
"which tools exist" a function of something a person can change underneath a
running conversation. It also produces the worse experience: a plugin that
silently is not there teaches nothing, where a run that stops and names the
missing value teaches exactly the right thing.

**A `[settings]` file inside the plugin's own directory.** Simplest to write
and wrong twice: reinstalling or updating the plugin overwrites it, and a
backup or a `git push` of a plugin directory carries the secret out with it.

**A new `plugin_settings.py` module.** Considered and dropped: it would hold
one pure naming function, one `os.environ.get`, and one list comprehension.
`plugins.py` already resolves `SADANA_PLUGINS_DIR` from the environment, so
this is the same kind of thing in the place that kind of thing already lives.
CLAUDE.md's rule that an I/O module is its own file names disk, network and the
clock; an environment read is none of those, and splitting for it would leave
`_plugins_root()` on the wrong side of the split it created.

**A `SettingsBackend` protocol, so a future secret manager could be dropped
in.** A seam for a family of one, which CLAUDE.md refuses by name. The second
member does not exist and is not being built.

**Fixing the plugin-name hole in a separate work item.** Tempting, because
requirement 2's plugin-name check is strictly wider than this item needs. Kept
here because this item is what first turns a plugin's name into an *identifier*
rather than only a path, and because the check costs one line in a function
every path already calls. Splitting it would mean shipping the identifier use
before the check that makes it safe.

## Concerns

**The policy conflict, stated plainly.** CLAUDE.md says secrets go in `.env`
and behaviours go in the config file, with the user-facing setting being the
config key. This design puts *both* kinds in `.env`. It does so because there
is no config file: `config_dir` holds exactly one thing today, `persona.md`,
and every behavioural setting in this project — `SADANA_STATE_DIR`,
`SADANA_PLUGINS_DIR`, `SADANA_GATEWAY_*` — is an environment variable read
through `config.env*`. Honouring the policy literally would mean inventing a
config-file loader whose only consumer is this feature, which the same document
refuses under "a seam for a family of one" and which guideline 2 calls a
speculative bet. I followed the smaller bet and I am not comfortable with it.
The cost is real: when a config file does land, non-secret plugin settings
belong in it and will have to move. The mitigation is that the design already
marks exactly which ones those are — every setting with `secret = false` is the
migration set, computable by a one-line query rather than by reading each
plugin. A reviewer who thinks the config file should be built first is making a
defensible argument and should say so now rather than after.

**Namespacing is a convention, not an enforcement, and the design must not be
read as claiming otherwise.** A `call` node's body runs in-process
(`plugin_blueprint.md` §10 Risk 1), so nothing stops it calling `os.environ`
directly and reading another plugin's value, or the host's
`OPENROUTER_API_KEY`. The intent's constraint "a plugin reads its own values
and no other plugin's" is therefore honoured as *addressing*, not as
*isolation*: there is a correct way to read your own value and it is
distinguishable from every other read, which is what a future scanner or vetting
step needs and what hermes gave up. Real isolation waits on the subprocess
primitive — blueprint §5.6 gap 1, §7 OQ3 — and this work item must not be cited
later as having provided it.

**The editor is the round trip most likely to break, and least likely to be
noticed.** `editor_server.py` sends `manifest_to_dict` to a browser and takes a
dict back to `manifest_from_dict`. If the page's own JavaScript rebuilds the
object from the fields it knows about rather than mutating what it received,
saving a plugin in the editor silently deletes its settings — and nothing fails
at save time; it fails on the next run, as a missing value the person already
set. Nothing in this repo can test JavaScript (CLAUDE.md), so this cannot be
covered by a unit test and has to be read and then proved by hand. It is the
first acceptance criterion I would check personally.

**Requirement 2 will reject a plugin that validates today.** A plugin named
with a capital letter, an underscore, or a dot goes from valid to invalid.
Nothing in the tree today has such a name — the builtin is `memory`, the
fixtures are `plugin-a` through `plugin-d` — and nothing is installed anywhere
yet, so the blast radius is believed to be zero. "Believed" is doing work in
that sentence: if the owner has a plugin outside this repository, it may stop
loading.

**An empty value and an unset value are deliberately the same thing.** Setting
a variable to the empty string reads as missing. That is right for a key and
arguably wrong for a setting whose meaningful value is "nothing"; no such
setting exists yet, and conflating them means the preflight cannot be defeated
by a blank line in `.env`. Recorded because it is a decision, not an oversight.

**A rule that binds beyond this work item.** Requirement 3 and the `## Design`
section establish something future work items will have to obey, and it is
quoted for the owner in the conversation with a proposed CLAUDE.md amendment.
