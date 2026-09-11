# Plan: Driving a browser someone else wrote (from intent.md 2026-09-11)

## Files that change

    src/sadana/browse.py                (new)  AGENT_SCRIPT, build_argv,
                                               summarise, model(), timeout_s()
                                               — the pure half
    src/sadana/builtin_plugins/browse/plugin.toml      (new)
    src/sadana/builtin_plugins/browse/browse.py        (new)  the body
    src/sadana/builtin_plugins/browse/schema/browse.json (new)
    scripts/prove_browse.py             (new)  a real browser, a real page
    tests/unit/test_browse.py           (new)  every branch of the pure half
    tests/unit/test_builtin_plugin_browse.py (new)  the plugin's own graph
                                               with run_program stubbed

    docs/reference/capability_import_blueprint.md   already corrected, before
                                               this plan was written — §4.2's
                                               evidence was false and the
                                               design depends on it being right
    src/sadana/plugins.py                (added)  required_setting(), written
                                               once because three bodies were
                                               each carrying it
    src/sadana/untrusted_text.py         (added)  defang_block() and fenced(),
                                               the second lifted out of
                                               web_search on its second caller
    src/sadana/builtin_plugins/web-search/search.py   (added)  uses the shared
    src/sadana/builtin_plugins/image-gen/draw.py      (added)  preflight helper

Nothing seeds this plugin: `builtin_seed.seed_all()` takes the directory
listing. `execution.py` is *not* touched — see Risks.

## Order of work

1. **`browse.py`, alone.** `AGENT_SCRIPT` as a constant, `build_argv`,
   `summarise`, the two config accessors. No callers. Tests:
   `tests/unit/test_browse.py`.

2. **The plugin directory.** `plugin.toml` (name `browse`, one entry, one
   `call` node, one `[[setting]]` with the `# pragma: allowlist secret` the
   commit hook needs), `schema/browse.json`, and `browse.py` as the body —
   named for what it does, since a body called `init.py` in a hyphenated
   directory breaks mypy and this one is not hyphenated but the convention now
   is. Tests: a `run_graph` walk with `execution.run_program` stubbed.

3. **`scripts/prove_browse.py`.** Run it once against a real page.

4. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**`execution.py` is deliberately untouched and that is the thing to check.**
`SUBPROCESS-01` listed stdin as a non-goal with "the moment one does need it,
it is a field and a test". The reference's interface *does* need stdin — it
pipes code — and this design does not, because the task travels in `argv`. If
step 2 finds itself wanting stdin, that is a signal the design drifted back
toward shipping model-written code, not a signal to add the field.

**What this could break, by name.** Nothing. Every file is new except the
blueprint, already corrected. No shared module changes, no existing test
touched. If any existing test changes behaviour, something was not additive.

**The most risky step is 1, and specifically `AGENT_SCRIPT`.** It is code held
as a string, so nothing type-checks it, nothing lints it, and its first real
execution is step 3. The mitigation is that it is *short* and that step 3 runs
it for real before anything is claimed — a script constant that has never been
executed is a guess.

**Where this could drift back into something `spec.md` rejected.** Two places,
and both will look like helpfulness.

Building any part of the script from the task will look like flexibility —
passing a starting URL, say. The script is a constant and the task is an
argument; anything else is the injection this design exists to avoid.

Letting the model pass code "just for hard cases" is exactly `browser_exec`,
which is the Shape C this item declined and the blueprint now records as its
own correction.

**What has no mitigation.** browser-use decides its own actions with the
person's access, and nothing here contains it — `spec.md` § Concerns says the
containment argument is social. And step 3's evidence costs model calls each
run, like the image one, so it is a photograph nobody re-takes.

## Proof

`tests/unit/test_browse.py` covers: `build_argv` placing the task in its own
element, with a task containing a quote, a semicolon, a newline and a leading
dash surviving as exactly one argument and never appearing inside the script
element; `AGENT_SCRIPT` containing no `%`, no `.format(`, and no f-string
marker, asserted as a property of the constant rather than by reading the
source file — it is a string in this module's namespace, so this is a value
assertion, not the source-reading `testing-conventions` bans; `summarise`
defanging an ANSI escape and a zero-width character out of what a page
reported, and reporting a non-zero exit with what the program said; the two
config accessors following their keys and their defaults.

`tests/unit/test_builtin_plugin_browse.py` covers: `validate()` returning
`Valid` for the shipped directory, which is what proves the manifest, the
schema and the body all resolve; a `run_graph` walk with
`execution.run_program` stubbed to a successful `Ran` returning the summary;
one stubbed to a `Failure` returning a plain sentence; one stubbed to a
non-zero `Ran` returning a plain sentence that carries the program's own
stderr; the request the body actually built carrying the task as its own argv
element and a timeout longer than the program default; and the key travelling
in `request.env` rather than being inherited.

`scripts/prove_browse.py` output, pasted whole into `review.md` § Evidence,
showing the task given, the page reached, and what the agent reported — a real
browser on a real page, because a stubbed one proves nothing about whether the
script constant works.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

**Three shared modules were touched that this plan said would not be.** All
three came out of the self-check and all three are the second-or-third-member
case CLAUDE.md names, not scope creep looking for a justification:

`plugins.required_setting()` — the "no no-key branch, the preflight already
refused it" assertion was written identically in three plugin bodies, two of
them with the same eight-line comment character for character and this item's
a shortened third wording. Now once, in the module that owns settings.

`untrusted_text.fenced()` — `web_search` invented a labelled, non-forgeable
fence for search snippets. `summarise` returned a page's own words *unlabelled*,
so they read as this plugin's own report — and `spec.md` itself argues browse is
the more exposed of the two, since the text was chosen by software rather than
by a person. The fence moved to `untrusted_text` on its second caller and
browse uses it.

`untrusted_text.defang_block()` — `defang` collapses all whitespace, which is
right for a 500-character snippet and wrong for an 8,000-character page report:
a table or several paragraphs arrived as one line. `strip_invisible` was
already keeping `\t\n` and then throwing them away two lines later.

**`build_env` moved into the pure half.** The name `BROWSE_MODEL` was written
in the plugin body and read inside `AGENT_SCRIPT` in a different file, with no
checker between them and a rename in one silently breaking the other. Both ends
now sit within a few lines of each other.

**A test that could not fail was replaced by one that can.** Three greps for
`%s`, `.format(` and `{task` in `AGENT_SCRIPT` cannot catch the one way it
could really become interpolated, and could not fail otherwise. `compile()`
catches what this plan calls the item's biggest risk — a syntax error in a
string constant staying invisible until the proof script runs.

**And a finding that is not this item's to fix.** `builtin_seed.seed()` returns
early when the destination exists, so a shipped plugin is materialised once and
never again. That is deliberate — a test pins that a person's local edit
survives — but it means every correction made to `web-search` and `image-gen`
in this very diff will never reach a machine that has already run the agent.
Observed directly: `sadana plugin settings browse` printed the previous wording
of a purpose seconds after it was changed. Owed as its own work item.
