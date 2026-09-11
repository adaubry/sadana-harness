# Plan: The first plugin that makes a file (from intent.md 2026-09-11)

## Files that change

    src/sadana/untrusted_text.py            (new)  defang and strip_invisible,
                                                   moved out of web_search.py —
                                                   see the amendment below
    src/sadana/web_search.py                       imports them from there
                                                   instead of defining them
    pyproject.toml                                 mypy excludes plugin bodies,
                                                   on the precedent already in
                                                   that file
    src/sadana/image_gen.py                 (new)  build_request,
                                                   extract_data_uri, decode,
                                                   filename — the pure half
    src/sadana/execution.py                        HttpRequest.timeout_s, an
                                                   optional per-request
                                                   override of the config
                                                   default
    src/sadana/builtin_plugins/image-gen/plugin.toml      (new)
    src/sadana/builtin_plugins/image-gen/init.py          (new)
    src/sadana/builtin_plugins/image-gen/schema/draw.json (new)
    scripts/prove_image_gen.py              (new)  the real round trip, and
                                                   the real file
    tests/unit/test_image_gen.py            (new)  every branch of the pure
                                                   half
    tests/unit/test_builtin_plugin_image_gen.py (new)  the plugin's own graph
                                                   through run_graph with
                                                   run_http stubbed, writing a
                                                   real file into a tmp output
                                                   directory
    tests/unit/test_execution.py                   the timeout override, and
                                                   that absent means default

Nothing seeds this plugin explicitly: `builtin_seed.seed_all()` takes the
directory listing, so placing the directory is the whole of it. That is the
property `WEB-SEARCH-01`'s self-check built it for, and
`test_builtin_seed.py`'s "everything seeded validates" test covers the new
plugin for free.

## Order of work

1. **`image_gen.py`, alone.** All four functions, no callers. The suite must
   be exactly as green as before. Tests: `tests/unit/test_image_gen.py`.

2. **`execution.HttpRequest.timeout_s`.** A defaulted trailing field; `None`
   means the config value, unchanged. Landed before anything needs it so a
   failure here is unambiguous. Tests in `tests/unit/test_execution.py`,
   alongside the ones that must keep passing unedited.

3. **The plugin directory.** `plugin.toml` (name `image-gen`, one entry, one
   `call` node, one `[[setting]]`, `# pragma: allowlist secret` on the
   `secret = true` line or the commit hook refuses it), `schema/draw.json`,
   and an `init.py` that gets the directory from `artifact_store.output_dir()`,
   writes the bytes, and returns an `Artifact`. Tests: a `run_graph` walk with
   `run_http` stubbed, asserting a real file on disk and that the base64
   appears nowhere in the `DagResult`.

4. **`scripts/prove_image_gen.py`.** Run it once against the real service.
   Keep the output, including the file's size and magic bytes.

5. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**What this could break, by name.** `execution.HttpRequest` is constructed in
`web_search`'s plugin body, in `model_providers/openrouter`, and across the
`execution` and plugin test files. A trailing defaulted field keeps every one
compiling and behaving identically — the same discipline `Manifest.settings`
and `Pause.turn_seq` both needed. If any existing `execution` test changes
behaviour, the default is wrong.

`artifact_store.output_dir()` has never been called by real plugin code. Step
3 is its first user, so a mistake in `ARTIFACT-STORE-01` surfaces here rather
than there — and that is a finding to report, not to quietly patch around.

**The most risky step is 3, and specifically the assertion that the bytes do
not travel.** It is easy to write a body that works perfectly and returns the
data URI somewhere incidental — in a success message, in the artifact's
`name`, in a log line. The test that pins it must search every field of the
`DagResult`, not just `text`, or it will pass while the thing it guards is
broken. `WEB-SEARCH-01` shipped exactly that kind of hole — sanitising moved
and nothing pinned it — and the cold review caught it, not the suite.

**Where this could drift back into something `spec.md` rejected.** Three
places, all tempting for the same reason: they make the feature nicer.

Reading the host's `OPENROUTER_API_KEY` will look like an obvious kindness the
first time the duplicate-key friction is felt. It is the spec's most-argued
decision and `intent.md` records it as the owner's fork.

A fallback model will look like robustness the first time the configured id
returns a 404. `spec.md` calls a fallback a second backend with a hat on.

A `write_artifact()` helper in `artifact_store` will look obviously right
while writing step 3's two lines. It has one caller until video generation
lands; that is when it earns its place.

**What has no mitigation.** Step 4 costs money each time it runs, so the
evidence is a photograph nobody will re-take. And this work item ships an
unbounded, growing store of files with nothing that deletes them — stated
three times across the chain and still true.

## Proof

`tests/unit/test_image_gen.py` covers: `build_request` producing
`modalities: ["text", "image"]`, the configured model id, and a body with the
prompt in it; the key absent from the URL. `extract_data_uri` returning the
URI for a reply shaped like the reference's, and a sentence — not an
exception — for a body that is not JSON, one with no `choices`, one whose
message has no `images`, and one whose `images` list is empty. `decode`
returning bytes and `png`/`jpg` for those media types, and a sentence for a
string that is not a data URI, for an unknown media type, and for base64 that
will not decode. `filename` turning a prompt containing `../`, a slash, a
leading dash and a non-ASCII character into one safe path component, and
never the empty string.

`tests/unit/test_execution.py` covers: `HttpRequest` with no `timeout_s`
using the config default; one with `timeout_s` set passing that value to the
opener instead; and every test already in that file passing unedited, which
is the proof step 2 changed nothing.

`tests/unit/test_builtin_plugin_image_gen.py` covers: `validate()` returning
`Valid` for the shipped directory; a `run_graph` walk with `run_http` stubbed
to a recorded-shape reply writing a file that really exists, inside the
directory `output_dir` names, whose bytes are the decoded ones; the returned
`Artifact` having `kind="file"` and a `ref` pointing at that file; the base64
payload appearing in **no** field of the `DagResult` — text, every
`NodeTrace.detail`, and the artifact's own `name` and `ref`; a reply carrying
no image producing a sentence and no file; and a run whose body would write
outside the run's directory failing its node, which exercises
`ARTIFACT-STORE-01`'s containment check rather than re-implementing it.

`scripts/prove_image_gen.py` output, pasted whole into `review.md` §
Evidence, showing the prompt, the path written, the file's byte count, and its
leading magic bytes — because "a request succeeded" is not the claim;
"a picture exists on disk" is.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

**The first version of this section described a change that did not ship, and
the cold review caught it.** It recorded `pyproject.toml` gaining a mypy
exclusion for plugin bodies, said this plan had named no config file, and said
the cost was that bodies lose static checking. All three were false by the time
the diff settled: the plan does name `pyproject.toml`, the exclusion was tried
and then *reverted* during the self-check, and bodies keep their checking.
CLAUDE.md says never to write a plausible trace for work you did not do; that
is what it had become, and it is replaced below rather than patched.

What actually happened, in order:

**`defang` moved twice.** First out of `web_search.py` into a new
`untrusted_text.py`, because this plugin needed the same filter — two real
consumers, so the seam earned its cost rather than being anticipated. Then the
*call* moved too: the self-check pointed out that `execution.run_http` is where
a stranger's response body becomes a string a model reads, and that both plugin
bodies were separately remembering to sanitise it. A third body would have
silently forgotten. `image_gen.defang_detail` was deleted and the guard now sits
in `run_http`. The cold review then found that placement covered only half the
string it composes — the status reason phrase is equally third-party — so it
moved once more onto the composed value.

**`extract_data_uri` and `decode` merged into `picture()`.** They were never
called apart, and split, the first handed its outcome back as a string the
caller had to sniff for a `data:` prefix — a string convention where its own
sibling used a typed one. `build_request(prompt, model)` likewise became
`build_request(prompt)` plus `model()`. `spec.md` § Interface still describes
the older shape; that is corrected there.

**Both hyphenated plugins' bodies were renamed** — `init.py` to `search.py`
and `draw.py`, with `body =` updated in each manifest. A plugin directory
carrying a hyphen is not a valid package name, so mypy treats its body as a
*top-level* module named after the file, and two plugins both shipping
`init.py` collide. The first fix excluded the whole directory from mypy; the
self-check showed the collision was only ever between the two hyphenated
plugins — `memory/init.py` resolves as a real package and was being checked —
so the exclusion dropped coverage from three bodies to solve a clash between
two files. Unique basenames remove it, and the constraint is recorded in
`pyproject.toml` so the next plugin does not reintroduce it.

**Seeding moved to `cli.main()`.** A first-run trap surfaced in use, not in
testing: `plugin set` refuses a plugin that is not installed, a builtin only
appeared once `chat` had seeded it, and `chat`'s first run is exactly what
fails for want of a key — with no way out but calling the seeder by hand. The
first fix put the seeding inside `_installed()`, a lookup function; the
self-check pointed out that patched the two doors noticed and left `editor`
with the same hole. One line in `cli.main()` covers every subcommand. The cold
review then found it had been placed *before* `parse_args`, with a comment
claiming it ran after — so `--version` and `--help` created directories and
could fail on an unwritable state dir. Moved below `parse_args`.

**Eight files this plan did not name were touched**, all of them consequences
of the above: `src/sadana/cli.py`, `src/sadana/subcommands/{chat,gateway,
plugin}.py`, `src/sadana/builtin_plugins/web-search/{plugin.toml, init.py →
search.py}`, `tests/unit/test_cli.py`, and
`tests/unit/test_builtin_plugin_web_search.py`.

**One Proof item names the wrong file.** This plan promises a test that a body
writing outside the run's directory fails its node, in
`test_builtin_plugin_image_gen.py`. The behaviour is covered — by
`test_plugin_manifest.py`'s `test_a_file_artifact_pointing_outside_the_directory
_fails_its_node`, which predates this item — and `filename()` makes it
unreachable from this plugin without a monkeypatch. Accurate coverage, an
inaccurate Proof sentence.
