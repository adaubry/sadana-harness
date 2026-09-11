# Spec: The first plugin that reaches the outside world

Intent: docs/tasks/WEB-SEARCH-01-the-first-plugin-that-reaches-the-outside-world/intent.md

## Requirements

1. **A `web-search` plugin exists and is reachable as a tool.** One entry, one
   `call` node, shipped as a first-party plugin and materialised into the
   plugins root the way `memory` already is. *(intent: "a plugin, built and
   installed the same way any other plugin would be".)*
2. **It takes a query and an optional result count, and answers with a
   numbered list of title / URL / description.** *(intent: "a handful of
   results out, each with a title, an address, and enough text".)*
3. **Its account key is a declared `[[setting]]`**, supplied by the person
   through `sadana plugin set`, never present in this repository. This is the
   first real consumer of `PLUGIN-CONFIG-01`. *(intent Constraints.)*
4. **One service, reached directly** — no provider seam, no second backend, no
   arrangement anticipating one. *(intent; capability blueprint §6 Rule 1.)*
5. **No new dependency.** The request is made with `execution.run_http` and
   the standard library. *(intent: "Nothing new gets installed".)*
6. **Everything except the round trip is unit-tested without a network.** The
   pure half — building the request, parsing and rendering a response — is its
   own module with no I/O. *(CLAUDE.md's I/O-module rule and
   `testing-conventions`' network ban.)*
7. **The real round trip is proved by a standalone script outside `make
   test`**, whose output is pasted into `review.md` § Evidence. *(CLAUDE.md:
   "Prove a block's first real external round trip with a standalone script…
   rather than relaxing testing-conventions' network ban".)*
8. **Results are rendered as data, never as instruction**, and defanged of the
   characters that hide text from a person while a model still reads it.
   *(intent's most important constraint.)*
9. **A missing key, an HTTP failure, a body that is not JSON, and zero results
   each produce a plain sentence**, not a traceback and not an empty answer.

## Design

A first-party plugin directory, a pure module beside it holding everything
that can be tested, and a thin body that does the one thing that cannot.

### Where the code lives

**`src/sadana/builtin_plugins/web-search/`** — `plugin.toml`, `init.py`,
`schema/search.json`. Same shape as `builtin_plugins/memory/`, which is the
only other first-party plugin and the pattern this follows rather than invents.

`plugin.toml` declares one entry (`web.search`), one `call` node, and:

The plugin's name is `web-search`, with a hyphen, and so is its directory.
`PLUGIN_NAME_RE` (added by `PLUGIN-CONFIG-01`) admits lowercase letters,
digits and single hyphens only, so `web_search` would fail validation and the
plugin would be silently excluded by `discover_plugins`. Its setting therefore
resolves as `SADANA_PLUGIN__WEB_SEARCH__API_KEY` — the hyphen becomes an
underscore in the variable name and nowhere else. The Python module beside it
keeps the underscore, since that one is imported.

```toml
[[setting]]
name = "api_key"
purpose = "Brave Search API key — free at https://brave.com/search/api/"
secret = true
```

**`src/sadana/web_search.py`** (new) — the pure half, and the whole reason
requirement 6 is satisfiable. No disk, no network, no clock:

- `build_url(query, count)` — the endpoint plus an encoded query string.
- `parse(body: bytes, limit: int) -> list[Result] | str` — a list of results,
  or one sentence saying what was wrong with the response.
- `render(results, query) -> str` — the text the model reads.
- `defang(text)` — requirement 8's character stripping.

**`src/sadana/builtin_plugins/web-search/init.py`** — the `call` body. Reads
the key with `plugins.read_setting`, calls `web_search.build_url`, makes the
one request through `execution.run_http`, hands the bytes to `web_search.parse`
and `web_search.render`. Every branch it can take is a one-liner delegating to
the pure module, which is what keeps the untestable part of this work item down
to "did the HTTP happen".

**`src/sadana/builtin_seed.py`** (new) — `seed(plugins_root, name)`, lifted
from `memory_store.ensure_plugin_seeded` unchanged except for taking the name.
This is a genuine second member, not a speculative seam: `memory` and
`web-search` both need materialising and the copy-into-staging-then-rename
logic is nine lines nobody should write twice. `memory_store.ensure_plugin_seeded` is **deleted**, not kept as a delegation.

*(Corrected at Deploy. This originally read "becomes a call to it, keeping its
own name so its two callers and its tests do not move." The self-check found
that justification void: both callers moved anyway when `web-search` was added
beside `memory`, and one memory test moved too. What shipped is
`builtin_seed.seed_all()`, driven by the directory listing rather than a list
anyone maintains, called once from each of the two subcommands — broader than
this spec described, and chosen because a shipped plugin nobody remembered to
seed fails silently.)*

**`scripts/prove_web_search.py`** (new) — requirement 7.

### The untrusted-text problem, and what is actually being claimed

This is the first time anything in this project puts text written by a stranger
in front of the model. The honest framing matters more than the mitigation,
because the mitigation is weak.

**What is done.** Results are rendered inside a delimited block introduced by
one line stating that the content is search-result text from third parties and
is information, not instruction. Each field is `defang`ed: ANSI escapes, C0/C1
control characters, zero-width characters and Unicode bidirectional overrides
are removed. Those are the characters whose entire purpose is to make what a
person reads differ from what a model reads, and stripping them is cheap and
deterministic. Each field and the whole block are length-capped, so a single
result cannot flood a turn.

**What is not claimed.** None of this prevents prompt injection. A result whose
description is a plain-English instruction reaches the model as plain English,
and framing is a convention the model may ignore. What the design does
guarantee is narrower and worth stating exactly: a search result reaches the
model **only** as a tool result, never as a system prompt and never as a skill.
`ask` builds a child's prompt from a `SkillRef` resolved out of `plugin.toml`
(`plugin_manifest.load_skill`), so nothing a search returns can become
instruction-shaped context. That is a structural property of the node
vocabulary, not a filter, and it is the only strong statement available here.

**What the reference does: nothing.** `tools/web_tools.py` and
`agent/web_search_provider.py` contain no sanitisation of result text and no
mention of injection; hermes's only injection thinking is at the browser
navigation layer (`tools/browser_tool.py:4186-4198` blocks URLs carrying
API-key-shaped strings, to stop exfiltration) and in its skill/plugin scanner.
Declined to copy the absence. Adopted the *shape* of hermes's exfiltration
concern instead: the key goes in a header, never a query parameter, so it
cannot end up in a logged or redirected URL.

### The backend, and why this one

Brave Search's free tier: `GET https://api.search.brave.com/res/v1/web/search`
with `q` and `count`, an `X-Subscription-Token` header and `Accept:
application/json` (`plugins/web/brave_free/provider.py:30,76-86`). `count` is
capped at 20 by the service; the plugin clamps before sending.

It is chosen on requirement 5 rather than on quality. Of hermes's eight
backends, the ones with better results need a Python SDK — Exa is `exa-py`
(`plugins/web/exa/__init__.py:3`), DuckDuckGo is the `ddgs` package
(`plugins/web/ddgs/plugin.yaml`) — and this project has no subprocess primitive
and no appetite for a dependency, so anything that is not a plain HTTP call
with a header is out. Brave is a plain HTTP call with a header. It also has a
free tier, which matters because requirement 7 needs a real key for a real
round trip.

**Declined from hermes's implementation:** `httpx`
(`plugins/web/brave_free/provider.py:69`) — `execution.run_http` is this
project's one outward path and takes the standard library only; the query
string is built with `urllib.parse.urlencode` rather than an HTTP client's
`params=`. And its `{"success": bool, ...}` return convention: CLAUDE.md holds
that a plugin's outcome crosses back as a returned value, and a node body
returns one value, so a failure here is a sentence the model reads, not a
status dict for a caller that does not exist.

### Which of the three moves this makes

This adds a step in the only sense that matters — a new plugin — and changes
nothing already on any path. `execution.run_http`, `run_graph`, the approval
gate, and the settings mechanism are all used exactly as they are, which is the
point: the work item's real deliverable is finding out whether they suffice.
The one shared thing it touches is `memory_store.ensure_plugin_seeded`, and
that is a move, not a change.

### Policies applied

- **`testing-conventions`** — the network ban is honoured, not relaxed:
  requirement 6 exists to make it honourable, and requirement 7 is how the
  round trip gets proved instead. No test reads source text; no fixture fakes
  a provider response shape that has not been observed — the shapes asserted
  come from the reference implementation and from the script's real output.
- **CLAUDE.md, "a module that touches real I/O is its own file, separate from
  a block's pure-function module"** — `web_search.py` versus `init.py`.
- **CLAUDE.md, "prove a block's first real external round trip with a
  standalone script"** — requirement 7.
- **CLAUDE.md, "a registry or dispatch seam… earns its cost only once a second
  real member exists"** — no provider seam (one backend); but `builtin_seed`
  *is* extracted, because materialising a shipped plugin now has two.
- **CLAUDE.md, "a plugin's outcome crosses back only as a returned
  DagResult"** — every failure is a returned sentence.
- **CLAUDE.md, "`_sadana_`-prefixed reserved key"** — not used; this plugin
  needs no execution identity, only a setting, which it reads for itself.
- `project-structure` and `reference-lookup` are not present in
  `.claude/skills/`; the corpus was consulted by hand, recorded above.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| the API key | stored, in `state_dir/.env` | a person typed it; `PLUGIN-CONFIG-01` owns it |
| the endpoint | a constant | one service, and a constant that changes is a new version of the plugin |
| a search's results | derived, never stored | no cache: a cache needs an invalidation answer, and "the web changed" has none |
| the seeded plugin directory | stored, on disk | already how `memory` works |

Nothing is cached, nothing has a lifetime, and a second search for the same
query makes a second request. A result cache is a real optimisation and a real
correctness question, and it is not this work item's.

## Interface

**`plugin.toml`:** entry `web.search`, parameters `schema/search.json`:
`query` (string, required), `count` (integer, 1–20, default 5).

**`src/sadana/web_search.py`:**

- `Result(title: str, url: str, description: str)` — frozen.
- `build_url(query: str, count: int) -> str`. Clamps `count` to 1–20.
- `parse(body: bytes) -> tuple[Result, ...] | str` — results, or one sentence
  naming what was wrong. Takes no limit: the service was already asked for
  `count`, and `render` bounds what a model reads. *(Corrected at Deploy; this
  originally specified a `limit` parameter.)*
- `render(query: str, results: tuple[Result, ...]) -> str` — the delimited,
  defanged, capped block.
- `defang(text: str) -> str`.

**The node body** returns a string in every case. No exception escapes it that
`run_graph` has to catch, though `run_graph`'s blanket handler remains the
backstop it already is for every other plugin.

**Failure sentences**, each plain and each naming what the person can do:
no key set; the service refused the key; the service could not be reached; the
response was not something this plugin understands; no results.

## Acceptance criteria

- [ ] `sadana plugin settings web-search` lists `api_key` as a secret, and
      `NOT SET` on a fresh instance.
- [ ] With no key set, a run of the plugin fails before the first node with a
      message naming `api_key` — the `PLUGIN-CONFIG-01` preflight, not a
      check written again here.
- [ ] `build_url` percent-encodes a query containing `&`, `=`, a space and a
      non-ASCII character, and clamps `count` to 20 when asked for 100.
- [ ] The API key appears nowhere in the URL `build_url` returns.
- [ ] `parse` returns results for a recorded-shape success body; returns a
      sentence for a body that is not JSON, for JSON that is not an object,
      and for a body with no `web.results`; returns an empty tuple for a
      genuine zero-result response.
- [ ] `render` strips an ANSI escape, a zero-width space and a bidi override
      from a title and a description, and caps a 10,000-character description.
- [ ] `render`'s output states that the block is third-party text and is
      information rather than instruction.
- [ ] A `plugin.toml`-driven run through `run_graph` with a stubbed
      `run_http` produces the rendered text — the plugin's own graph is
      exercised, not just the functions.
- [ ] `memory`'s seeding behaviour is unchanged, proved by its existing tests
      passing unedited.
- [ ] `scripts/prove_web_search.py` performs a real search and prints results;
      its output is in `review.md` § Evidence.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Reading the contents of a page (hermes's `web_extract`). The obvious next
  thing, deliberately out — `intent.md` § Open questions.
- A second search backend, or any seam for one.
- Caching, rate limiting, retry, or pagination.
- Changing the approval gate's behaviour for `call` nodes — `intent.md` raises
  the friction as the owner's question and this item changes nothing.
- Any claim to prevent prompt injection. See Design.
- Installing this plugin through `plugin install` from a git remote; it is
  first-party and seeded, like `memory`.

## Open questions

**Should the free tier's rate limit be handled?** Brave's free tier is roughly
one query per second and 2,000 a month. Nothing here throttles, so a burst
gets an HTTP 429 and the person sees "the service refused". Handling it
properly means retry and backoff, which is a real mechanism and belongs with
whatever else needs one. The owner may want it sooner.

**Is `web.search` the right tool name?** It is dotted like `memory.remember`,
which is the only precedent. Nothing enforces or documents a naming convention
for entry tools, and the moment there are five plugins somebody will want one.

## Rejected alternatives

**A keyless backend (DuckDuckGo via `ddgs`).** Tempting: no signup, so the
intent's "the person has to go and get an account" friction disappears. It
needs a Python package, which requirement 5 forbids, and scraping-backed
endpoints break without warning. It would also throw away the only real test
of `PLUGIN-CONFIG-01`'s settings path, which `intent.md` § Changed during
planning records as the reason the friction was kept deliberately.

**A provider seam with Brave as the first implementation.** The shape hermes
has, and the single likeliest failure the capability blueprint names
(§9 Risk 1: "the registry reflex"). Declined by name.

**Putting the HTTP call and the parsing in one module.** Fewer files, and it
would make requirement 6 impossible: the parse and render logic would only be
reachable through a function that opens a socket, and the unit suite would
have to either mock the network or skip the logic. The split is what lets
every branch except the round trip be tested honestly.

**Returning a structured result rather than rendered text.** A node returns one
value and the model reads `DagResult.text`; a dict would be stringified by
`_coerce_text` into something nobody designed. Rendering deliberately, where
the defanging and the framing can be applied, is the point.

**Sanitising result text by stripping anything that looks like an
instruction.** Declined as security theatre: there is no reliable way to
distinguish a sentence about a topic from a sentence instructing about it, a
filter that half-works invites reliance on it, and the structural claim in
Design (a result can never become a system prompt or a skill) is worth more
than a pattern list that will be bypassed. Control characters are stripped
because that is a closed, decidable set; prose is not.

## Concerns

**The dangerous thing and the valuable thing are in the same work item, and
that is not ideal.** The point of this item is to test whether the plugin shape
holds for a real capability. The side effect is opening the first channel for
untrusted outside text to reach the model. Those deserve separate scrutiny and
they are arriving together because the capability cannot be tested without the
channel. If a reviewer wants to read only one part of this diff carefully, it
is `render` and what calls it.

**The strong claim in Design rests on the node vocabulary staying as it is.**
"A search result can never become a system prompt or a skill" is true because
`ask` resolves its skill from `plugin.toml` and nowhere else. It is one node
kind away from being false — a future kind that built prompt text from a
threaded value would break it silently, and nothing would fail. That property
deserves to be a rule, and a CLAUDE.md amendment is proposed with this spec.

**Requirement 7's evidence is the weakest kind in this project.** It is a
script run once, by me, on my machine, against a real service, and pasted in.
It cannot be re-run by a reviewer without their own key, it is not in CI, and
nothing stops it rotting. That is the trade CLAUDE.md already chose over a
network-touching unit test, and it is right, but the evidence is a photograph
rather than a check.

**The approval gate will make this annoying and I am shipping it anyway.**
Every search will prompt. `intent.md` records it as the owner's question and
this spec does nothing about it, which means the first honest use of this
feature is also the first time that friction is felt. That is arguably the
correct order — the friction is now a real complaint rather than a predicted
one — but it is a deliberate choice to ship something known to be irritating.

**Brave's result quality is not being evaluated.** The backend was chosen on
"plain HTTP, no dependency, has a free tier". If the answers turn out to be
poor, that is a product finding this item cannot see, and swapping backends
later means building the seam this spec declines to build now — at which point
the decision is cheap, because there will genuinely be two.
