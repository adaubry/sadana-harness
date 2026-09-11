# Plan: The first plugin that reaches the outside world (from intent.md 2026-09-11)

## Files that change

    src/sadana/web_search.py                (new)  Result, build_url, parse,
                                                   render, defang — the pure
                                                   half, no I/O anywhere
    src/sadana/builtin_seed.py              (new)  seed(plugins_root, name),
                                                   lifted from memory_store
    src/sadana/memory_store.py                     ensure_plugin_seeded becomes
                                                   a call to it; name and
                                                   behaviour unchanged
    src/sadana/builtin_plugins/web-search/plugin.toml   (new)
    src/sadana/builtin_plugins/web-search/init.py       (new)
    src/sadana/builtin_plugins/web-search/schema/search.json (new)
    src/sadana/subcommands/chat.py                 seeds web-search alongside
                                                   memory
    src/sadana/subcommands/gateway.py              the same, second call site
    scripts/prove_web_search.py             (new)  the real round trip
    tests/unit/test_web_search.py           (new)  every branch of the pure
                                                   half: encoding, clamping,
                                                   each parse failure, defang,
                                                   capping, the framing line
    tests/unit/test_builtin_seed.py         (new)  seeding and idempotency for
                                                   a name other than memory
    tests/unit/test_builtin_plugin_web_search.py (new)  the plugin's own graph
                                                   through run_graph with
                                                   run_http stubbed
    tests/unit/test_memory_store.py                unchanged — that it still
                                                   passes is the proof step 2
                                                   was a move
    tests/unit/test_builtin_plugin_memory.py (added) repointed at the moved
                                                   constant — see the
                                                   amendment below

## Order of work

1. **`web_search.py`, alone.** Every function, no callers. The suite must be
   exactly as green as before. Tests: `tests/unit/test_web_search.py`.

2. **`builtin_seed.py`, the extraction.** Move the nine lines out of
   `memory_store.ensure_plugin_seeded`, which becomes a one-line delegation
   keeping its own name so its two callers and its tests do not move. The proof
   this was a move is that `tests/unit/test_memory_store.py` passes without
   being edited.

3. **The plugin directory.** `plugin.toml` (name `web-search`, one entry, one
   `call` node, one `[[setting]]`), `schema/search.json`, and an `init.py`
   whose every branch delegates to step 1. Tests: a run through `run_graph`
   with `execution.run_http` stubbed, which exercises the real manifest, the
   real graph and the real body.

4. **Seeding it.** Both call sites — `subcommands/chat.py` and
   `subcommands/gateway.py` — seed `web-search` next to `memory`.

5. **`scripts/prove_web_search.py`.** Run it against the real service with a
   real key; keep the output for `review.md` § Evidence.

6. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**The name would have failed validation and this was nearly missed.**
`PLUGIN_NAME_RE`, added by `PLUGIN-CONFIG-01` two commits ago, admits lowercase
letters, digits and single hyphens. `web_search` fails it, and the failure mode
is the quiet one: `discover_plugins()` silently excludes an invalid plugin, so
the tool would simply not appear and nothing would say why. Checked against the
live pattern before writing this plan rather than after. The plugin is
`web-search`; the Python module beside it keeps its underscore because that one
is imported.

**What this could break, by name.** `memory_store.ensure_plugin_seeded` is the
only shared thing touched. Its two callers (`chat.py:116`, `gateway.py:78`) and
its two tests must all keep working untouched; if any needs editing, step 2 was
not a move and is wrong.

Nothing else in the tree changes behaviour. `execution.run_http`, `run_graph`,
the approval gate and the settings preflight are used exactly as they are —
which is the point of the work item, so any friction found there is a finding
to report rather than a thing to patch around here.

**The most risky step is 3, for an unusual reason.** It is the first time the
pieces are assembled, so it is the step most likely to reveal that something
does not fit — and the temptation when that happens will be to reach into
`run_graph` or the manifest vocabulary and adjust it so the plugin works. That
is exactly backwards. If the plugin cannot be expressed, the finding is the
deliverable (`intent.md`), and it belongs in the report and a new `intent.md`,
not in this diff.

**Where this could drift back into something `spec.md` rejected.** Two places.

Step 3 will make a provider seam look sensible the moment a second service is
mentioned in passing — the capability blueprint names this as its single
likeliest failure (§9 Risk 1, "the registry reflex"). One backend, called
directly.

Step 1 will make a regex-based "strip anything that looks like an instruction"
filter look responsible, because the untrusted-text constraint is fresh in
mind. `spec.md` declines it as theatre: control characters are a closed,
decidable set and get stripped; prose is not and does not.

**What has no mitigation.** Step 5's evidence is a script run once, on one
machine, against a live third-party service, with a key that is not in CI. It
cannot be re-run by a reviewer without their own key and nothing stops it
rotting. `spec.md` § Concerns says so; it is the trade CLAUDE.md already chose
over a network-touching unit test.

## Proof

`tests/unit/test_web_search.py` covers: `build_url` percent-encoding a query
containing a space, `&`, `=` and a non-ASCII character; `count` clamped to 20
when 100 is asked for and to 1 when 0 is; the API key appearing nowhere in the
returned URL; `parse` returning results for a success body shaped like the
reference's, and a sentence — not an exception, not an empty list — for a body
that is not JSON, for JSON that is not an object, and for JSON with no
`web.results`; `parse` returning an empty tuple for a genuine zero-result
response, which is a different outcome from a malformed one; `defang` removing
an ANSI escape, a zero-width space and a bidi override; `render` capping a
10,000-character description and stating in its own text that the block is
third-party content and is information rather than instruction.

`tests/unit/test_builtin_seed.py` covers: seeding a named plugin into an empty
root, and a second call leaving an existing directory untouched.

`tests/unit/test_builtin_plugin_web_search.py` covers the plugin as a plugin:
`validate()` on the shipped directory returns `Valid` — which is what proves
the name passes `PLUGIN_NAME_RE`, the schema parses and the body resolves — and
a `run_graph` walk with `execution.run_http` stubbed to a recorded-shape
response produces the rendered text, with the approval gate stubbed to accept.
A second walk with `run_http` stubbed to a `Failure` produces the plain
sentence rather than a `failed_node`.

`tests/unit/test_memory_store.py` passes with no edit.

`scripts/prove_web_search.py` output, pasted whole into `review.md` §
Evidence, showing a real query and real titles and URLs coming back.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

**`tests/unit/test_builtin_plugin_memory.py` was edited, and this plan said no
memory test would need to be.** The claim was about `test_memory_store.py`,
which is indeed untouched and does prove step 2 was a move. But a *second*
memory test file reached into `memory_store._BUILTIN_PLUGIN_SOURCE` — a private
constant — to find the shipped plugin directory, and that constant genuinely
moved to `builtin_seed.SOURCE_ROOT`. Three tests were repointed at its new
home.

The alternative was leaving a vestigial alias in `memory_store` that exists
only so a test need not change, which is worse: it would preserve a private
name nothing uses and hide that the coupling was to an implementation detail
in the first place. Recorded because "no memory test is edited" was offered as
the proof of a clean move, and that proof is now weaker than advertised — one
file did change, for a reason that is about the test's coupling rather than
about behaviour.

**The plugin's name would have failed validation, and the plan caught it, but
`spec.md` did not.** `spec.md` was written naming the plugin `web_search`
throughout; `PLUGIN_NAME_RE` admits no underscore, so `discover_plugins` would
have silently excluded it and the tool would simply never have appeared. Caught
by running the live pattern before writing this plan, and `spec.md` was
corrected in place before any code was written. The Python module beside it
keeps its underscore, because that one is imported rather than validated.


**Three more deviations, found by the cold review rather than recorded when
they were made.** The self-check between writing this plan and the review
changed more than the plan says, and the plan is the record that has to be
honest about it:

- **`memory_store.ensure_plugin_seeded` was deleted, not kept.** This plan
  said it "becomes a one-line delegation keeping its own name so its two
  callers and its tests do not move", and offered `test_memory_store.py`
  passing unedited as the falsification test for step 2. Both halves are now
  false: the wrapper is gone, both callers changed, and `test_memory_store.py`
  lost its two seeding tests to `tests/unit/test_builtin_seed.py` — the file
  this plan promised and which the self-check produced as a side effect. Step
  2 was still a move; the proof of it is now that those two tests pass in
  their new home unchanged, which is weaker than "nothing was edited" and is
  stated here rather than left implied.
- **`builtin_seed.seed_all()` appears in no artifact before this line.** Step
  4 said "both call sites seed `web-search` next to `memory`". What shipped
  iterates the shipped-plugins directory, which is broader: anything a future
  commit drops into `src/sadana/builtin_plugins/` is seeded without a second
  edit. That is the point — a plugin nobody remembered to seed is silently
  absent — but it is a different behaviour from what was planned.
- **`parse` lost its `limit` parameter and `defang` changed shape.** Both from
  the self-check: the ceiling was expressed four times over and the request
  itself already set it, and the service's HTML markup moved out of `defang`
  into `parse` where the rest of that service's wire knowledge lives.
