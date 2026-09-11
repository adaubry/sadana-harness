# Spec: The first plugin that makes a file

Intent: docs/tasks/IMAGE-GEN-01-the-first-plugin-that-makes-a-file/intent.md

## Requirements

1. **An `image-gen` plugin exists and is reachable as a tool.** One entry, one
   `call` node, shipped and seeded the way `memory` and `web-search` are.
2. **It takes a description and writes one image file**, returning an
   `Artifact(kind="file")` whose `ref` is that file's path.
3. **The image's bytes never enter the conversation.** What crosses back is a
   path. *(intent Constraints — the constraint this item exists to get right.)*
4. **The file is written inside the run's own output directory**, obtained
   from `artifact_store.output_dir()`, and `run_graph`'s existing containment
   check refuses anything else — no second check written here.
   *(`ARTIFACT-STORE-01`; first real user of it.)*
5. **Its account key is a declared `[[setting]]`**, not the host's
   `OPENROUTER_API_KEY`. *(intent Open questions; decided — see Design.)*
6. **A caller may ask for longer than the default timeout**, because drawing
   takes tens of seconds and every previous caller of `execution.run_http`
   returned in under one. *(intent Constraints.)*
7. **One service, reached directly, no new dependency**, as with
   `WEB-SEARCH-01`. *(capability blueprint §6 Rule 1.)*
8. **Everything except the round trip is unit-tested without a network**; the
   pure half is its own module. *(CLAUDE.md's I/O-module rule.)*
9. **The real round trip is proved by a standalone script**, and because
   making a file is the half never before proven, the script must show the
   file existing, being non-trivially sized, and carrying an image's magic
   bytes — not merely that a request succeeded. *(intent.)*
10. **Every failure is a plain sentence**: no key, refused key, unreachable,
    a reply carrying no image, a reply this plugin cannot read, a file that
    could not be written.

## Design

A plugin directory, a pure module for everything decidable, a thin body, and
one small widening of `execution.run_http`. The two decisions worth arguing
with are the timeout and whose key this plugin uses.

### Where the code lives

**`src/sadana/builtin_plugins/image-gen/`** — `plugin.toml`, `init.py`,
`schema/draw.json`. Name hyphenated, because `PLUGIN_NAME_RE` admits no
underscore and an invalid plugin is *silently excluded* by `discover_plugins`
— the trap `WEB-SEARCH-01` nearly shipped with.

**`src/sadana/image_gen.py`** (new) — the pure half. No disk, no network:

- `build_request(prompt) -> tuple[str, bytes]` — the URL and the encoded JSON
  body, `modalities: ["text", "image"]`. The model comes from `model()`.
- `picture(body: bytes) -> tuple[bytes, str] | str` — the picture's bytes and
  a file extension, or one sentence. Reaches
  `choices[0].message.images[].image_url.url`, the shape
  `plugins/image_gen/openrouter/__init__.py:160-178` documents.

  *(Corrected at Deploy. This originally specified `extract_data_uri(body) ->
  str` and `decode(data_uri)` as two functions. They were merged during the
  self-check: never called apart, and split, the first returned a string the
  caller had to sniff for a `data:` prefix — a string convention where its
  sibling used a typed one.)*
- `filename(prompt, extension) -> str` — a readable, path-safe name.

**`src/sadana/execution.py`** — `HttpRequest` gains
`timeout_s: int | None = None`. `None` keeps the config default exactly as it
is for every existing caller.

**`scripts/prove_image_gen.py`** (new) — requirement 9.

### Why the plugin declares its own key, when the person already gave one

`sadana setup` collects `OPENROUTER_API_KEY`, and this plugin talks to
OpenRouter. Reading that variable would cost the person nothing and work
immediately. It is declined.

The reason is not tidiness. `PLUGIN-CONFIG-01` built a namespace precisely so
that a plugin reading its own credential is *distinguishable* from a plugin
reading somebody else's — its Concerns section is explicit that this is
addressing, not isolation, and that the value is in a future scanner or
vetting step being able to tell the two apart. A first-party plugin reaching
for the host's key would make "a plugin reads host credentials" the normal,
blessed pattern in the first example anyone copies. The third-party plugin
that does the same thing later would then look exactly like this one.

The cost is real and worth stating plainly: the person pastes the same key
twice, and it will look like a bug. `sadana plugin settings image-gen` names
the variable and says where to get the value, which is the mitigation, and it
is a weak one.

**This is the finding a reviewer is most likely to disagree with**, and
`intent.md` records it as the owner's fork. If it is overruled, the change is
one line in `init.py` and the precedent moves with it.

### The timeout, and why it widens a shared module

`execution.run_http` reads one timeout from `SADANA_EXECUTION_HTTP_TIMEOUT_S`,
default 30 seconds, chosen by `E1-call-node-execution` when the only caller
fetched a chat completion. Drawing takes tens of seconds and routinely exceeds
it.

Three options considered. Raising the global default punishes every other
caller, which is how a search that should fail fast hangs for two minutes.
Having the plugin set the environment variable mutates process state to pass
an argument. Adding `timeout_s` to `HttpRequest` lets the caller that knows
say so, and changes nothing for the callers that do not.

CLAUDE.md keeps behaviours in config, and the *default* stays exactly there —
this adds a per-request override, not a second source of truth. It is a
widening of a shared primitive inside a feature work item, the same shape as
`WEB-SEARCH-01`'s redirect fix, and it is named here rather than discovered.

### Why the bytes must not travel, and how that is guaranteed

An image comes back as a base64 data URI. A 1MB PNG is roughly 1.4MB of text.
The naive body returns it and *works* — correct file, correct path — while
silently consuming most of a conversation's remaining room.

The guarantee is structural rather than a rule anyone must remember. A node
returns exactly one value. This body returns an `Artifact`, and `run_graph`
records it and threads `artifact.ref` onward (`plugin_manifest.py`, the arm
`G2-artifacts` built), so the value that becomes `DagResult.text` is the path
and there is no channel by which the bytes could accompany it. The data URI
exists only inside the body, between the response and the file write.

A test asserts the base64 payload appears nowhere in the returned `DagResult`
— text, trace, or artifact ref.

### Which of the three moves this makes

It **adds a step** (a plugin) and **makes one existing step heavier** by one
optional field (`HttpRequest.timeout_s`, ignored when absent). It makes
nothing harder. The containment check on the written file already exists and
is not re-implemented: requirement 4 is satisfied by using
`artifact_store.output_dir()` and letting `run_graph` do what it already does,
which is the cheapest possible placement because it is no placement at all.

### What the reference does, and what is taken

hermes's OpenRouter image backend
(`plugins/image_gen/openrouter/__init__.py`) sends `modalities: ["text",
"image"]` to `/chat/completions` and reads
`choices[0].message.images[].image_url.url` (`:4-8`, `:160-178`). **Adopted**:
the request shape and the extraction path, which is wire knowledge worth
copying exactly rather than rediscovering.

**Declined:** its model chain with fallback (`:94-96`, a default plus a
`_FALLBACK_MODEL`, plus `_IMAGE_API_MODELS` routing and a live catalog fetch
at `:241-276`). That is a provider-selection subsystem, and this project has
one model and no picker. One model id, a config key to change it, no chain, no
catalog, no fallback — a fallback is a second backend wearing a hat
(§6 Rule 1).

**Declined:** reference images (`_to_image_url_part`, `:129-158`). It needs a
way to name a picture that already exists, which is the retention question in
disguise. `intent.md` scoped it out for that reason.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| the API key | stored, `state_dir/.env` | `PLUGIN-CONFIG-01` owns it |
| the model id | a config key with a default | a behaviour, not a secret (CLAUDE.md) |
| the image file | stored, in the run's output directory | it is the deliverable |
| the file's name | derived from the prompt | a readable name beats a stored one nothing reads |
| the run's directory | derived | `ARTIFACT-STORE-01` resolves it per call |

Nothing new is cached and nothing gains a lifetime — except the files, which
is precisely the unanswered question below.

## Interface

**`plugin.toml`:** entry `image.draw`, parameters `schema/draw.json`:
`prompt` (string, required). One `[[setting]]`, `api_key`, secret.

**`src/sadana/image_gen.py`:** the four pure functions above; each failure
path returns a sentence rather than raising.

**`src/sadana/execution.py`:** `HttpRequest.timeout_s: int | None = None`.

**The node body** returns `Artifact(kind="file", name=…, ref=str(path))` on
success and a `str` on every failure.

**Config:** `SADANA_IMAGE_GEN_MODEL`, defaulting to an image-output model id,
and `SADANA_IMAGE_GEN_TIMEOUT_S`, defaulting to 180 — a timeout is a
behaviour, and CLAUDE.md keeps behaviours in config. *(The second was added at
Deploy; it began as a constant in the plugin body.)*

## Acceptance criteria

- [ ] `validate()` on the shipped directory returns `Valid`, proving the name
      passes `PLUGIN_NAME_RE`, the schema parses and the body resolves.
- [ ] With no key, a run fails before the first node naming `api_key` — the
      `PLUGIN-CONFIG-01` preflight, not a check written again here. The body
      therefore carries no no-key branch at all: one was written, found
      unreachable at Deploy, and replaced with an `assert` that fires only if
      the preflight itself has stopped working.
- [ ] `build_request` sends `modalities: ["text", "image"]` and the configured
      model, with the key in an `Authorization` header and never in the URL.
- [ ] `extract_data_uri` returns the URI for a recorded-shape reply, and a
      sentence for: not JSON, no `choices`, a message with no `images`, and an
      empty `images` list.
- [ ] `decode` handles `image/png` and `image/jpeg`, returns a sentence for a
      non-data URI, for an unknown media type, and for undecodable base64.
- [ ] `filename` produces a path-safe name from a prompt containing a slash, a
      dot-dot, a leading dash and a non-ASCII character.
- [ ] A `run_graph` walk with `run_http` stubbed writes a real file into the
      run's output directory and returns an `Artifact` naming it.
- [ ] The base64 payload appears in no field of the returned `DagResult`.
- [ ] A body claiming a path outside the run's directory fails its node —
      exercising `ARTIFACT-STORE-01`'s check rather than re-implementing it.
- [ ] `HttpRequest(timeout_s=None)` uses the config default; a set value is
      used instead; every existing `execution` test passes unedited.
- [ ] `scripts/prove_image_gen.py` draws a real picture, writes it, and
      reports its size and magic bytes; output in `review.md` § Evidence.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Deleting anything. See Open questions; this item creates the cost.
- Editing a picture, variations, reference images, sizes, styles, or more
  than one image per call.
- A model picker, a fallback chain, or a live model catalog.
- Video (`blueprint §5.5 #5`), which is the same shape and waits for this.
- Showing the person the picture. A path is the deliverable; rendering is a
  client-surface concern this project does not have.

## Open questions

**What happens to old pictures.** Unanswered, and this is the work item that
makes it cost something. Three plausible answers — a total-size cap evicting
oldest first, an age limit, or nothing automatic plus a command that clears
them on request. Deliberately not picked: the right answer depends on what a
month of real use looks like, and guessing now would bake in a policy nobody
has evidence for. **This item owes a follow-up `intent.md`**, and shipping
without one is the single loosest thread in this chain.

**Is one model id enough?** A model that stops being served makes the plugin
dead until the config key is changed. The reference's answer is a fallback
chain; this spec calls that a second backend. If the id proves unstable the
answer is probably a clearer error message, not a chain.

## Rejected alternatives

**Reading the host's `OPENROUTER_API_KEY`.** Argued in Design; the one a
reviewer should push on.

**Returning the data URI and letting something downstream write the file.**
Puts the bytes in the value channel, which is the whole thing this design
avoids. It would also make the file somebody else's problem at exactly the
point the plugin knows most about it.

**Writing the file through a new helper in `artifact_store`.** Considered: a
`write_artifact(name, data)` that creates the directory, writes and returns an
`Artifact`. Declined for now — it has one caller, and `output_dir()` plus
`Path.write_bytes` is two lines. The moment video generation lands there will
be a second caller doing exactly this, and that is when the helper earns its
place (CLAUDE.md: a seam earns its cost once a second real member exists).

**Raising `SADANA_EXECUTION_HTTP_TIMEOUT_S` globally.** Makes every future
caller wait for the slowest one. A search that should fail fast would hang.

**A model chain with fallback, as the reference has.** Declined above.

**Proving the round trip with a mocked provider.** Defeats the point of
requirement 9: the half that has never been exercised is making a file, and a
mock proves nothing about whether the bytes were real.

## Concerns

**This work item knowingly creates an unbounded, growing cost and does not
fix it.** Nothing deletes; a person iterating on a prompt writes a file every
time. That is stated in `intent.md`, in Non-goals, and in Open questions, and
it is still the thing most likely to be regretted. The honest summary is that
retention has been deferred three times and this is the first time deferring
it costs anything real. A reviewer who wants a size cap before merge is making
a defensible argument.

**The key decision is a precedent, not a detail.** Declining to read the
host's `OPENROUTER_API_KEY` makes the first file-producing plugin also the
first to demand a duplicate credential. If that proves annoying enough to
reverse, it will be reversed under pressure, at which point "a plugin may read
the host's credentials" gets established by convenience rather than by
decision. Better to have the argument now, in writing, than then.

**Two work items running have now widened `execution.py`.** `WEB-SEARCH-01`
added redirect refusal; this adds a per-request timeout. Both were justified
individually and both were discovered by a feature needing them. That is a
pattern worth noticing: the HTTP primitive was specified against exactly one
caller and is being completed one feature at a time. A third widening should
prompt a deliberate look at it rather than a fourth.

**Requirement 9's evidence costs money every time it runs.** Unlike the search
proof, each run of `prove_image_gen.py` bills the owner's account. That makes
it the kind of check nobody re-runs, which makes it exactly as much of a
photograph as `WEB-SEARCH-01`'s was, and slightly worse.

**The file name comes from a model-supplied prompt.** It is sanitised, and the
containment check catches an escape regardless, so this is belt and braces
rather than a hole. Worth a reviewer's attention anyway: it is the first time
anything model-authored has influenced a filename in this project.
