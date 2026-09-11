# Spec: Driving a browser someone else wrote

Intent: docs/tasks/BROWSE-01-driving-a-browser-someone-else-wrote/intent.md

## Requirements

1. **A `browse` plugin exists**: one entry, one `call` node, taking a goal in
   plain language and returning what the browser found.
2. **It is entered once and exits once.** The model supplies a task string and
   nothing else; it does not issue steps. *(intent Constraints; capability
   blueprint §4.2 as corrected.)*
3. **browser-use is run as a program, never imported.** Nothing is added to
   this project's own dependencies. *(intent Constraints.)*
4. **The script it runs is first-party and fixed.** The model's contribution is
   one argument, passed as its own element of `argv`, never assembled into
   anything.
5. **It gets the environment it needs and not this project's.** Its model key
   is a declared `[[setting]]`; nothing else is inherited beyond what
   `run_program` already allows.
6. **The person is told what it costs**: the software, the browser, and that
   the pages it reads are sent to a model to decide the next step.
7. **Failures are plain sentences** — browser-use absent, no browser, no key,
   the task failing, the run taking too long.
8. **The real round trip is proved by a standalone script** outside `make
   test`, with its output in `review.md` § Evidence. *(CLAUDE.md.)*

## Design

A plugin, a fixed script, and one new field on `ProgramRequest`. The
interesting part is what the research found rather than what the code does.

### What the reference actually offers, and why this is not the planned import

The capability blueprint scheduled this as "take `browser_exec`", on the
reading that it takes a natural-language task. It does not: it takes `code`
(`tools/browser_use_cli.py:1057-1061`), piped to the CLI on stdin. The model
writes Python and issues it across calls — **Shape C by the blueprint's own
§4.3**, which says such a capability is imported by finding its Shape B form
or not at all. The blueprint's §4.2 has been corrected in place with the
original wording quoted, because it is cited by section number elsewhere.

The current CLI has no goal-shaped entry either. Its full command list is
code-on-stdin plus `doctor`, `auth`, `skill`, `recordings`, `video`,
`telemetry`, `--update`, `--reload`.

**The Shape B form is in the library, not the CLI.** `browser_use.Agent` still
takes `task=` and `llm=`, and `browser_use.llm.openrouter.chat.ChatOpenRouter`
exists — so the key already stored for `image-gen` works. Reached as:

```
uvx --from browser-use python -c "<fixed script>" "<the task>"
```

which is a *program*, so requirement 3 holds and `SUBPROCESS-01`'s primitive is
what runs it. `uvx` resolves browser-use into its own isolated environment;
nothing enters this project's dependency set.

### Where the code lives

**`src/sadana/builtin_plugins/browse/`** — `plugin.toml`, `browse.py`,
`schema/browse.json`. The body is named for what it does, not `init.py`, per
the constraint `pyproject.toml` now records.

**`src/sadana/browse.py`** — the pure half: `build_argv(task)`, `AGENT_SCRIPT`,
and `summarise(ran)` turning a `Ran` into what the model reads.

**`src/sadana/execution.py`** — `ProgramRequest` gains `stdin: bytes | None`.
Not needed by this design, which passes the task in `argv`… and that is worth
stating rather than adding: **it is not added.** `SUBPROCESS-01` listed stdin
as a non-goal with "the moment one does need it, it is a field and a test",
and this item does not need it. Recorded so the next reader knows the question
was asked.

**`scripts/prove_browse.py`** — requirement 8.

### The script is ours, and that is the whole security posture

`AGENT_SCRIPT` is a string constant in `browse.py`. It reads `sys.argv[1]` as
the task, constructs `ChatOpenRouter` and `Agent`, runs it, and prints the
result. The model never contributes code — only the task string, which arrives
as its own `argv` element and is never concatenated into the script.

That matters more here than anywhere else in this plan, because the alternative
the reference offers is literally "let the model write Python and run it". This
design declines that, and the decline is the difference between Shape B and
Shape C.

What is *not* claimed: browser-use itself decides what to do with the task, and
it is third-party code running with the person's own access. `run_program` says
plainly it is not a boundary, and `intent.md` records the owner's decision to
accept that. The pages it reads are sent to a model — a second exposure, named
in requirement 6 and in the entry's own `purpose` so the agent repeats it.

### Which of the three moves this makes

It **adds a step** — one plugin — and changes nothing existing. `run_program`,
`artifact_store`, the settings mechanism and the approval gate are used exactly
as they are. That is the third consecutive import to need no change to the
plugin machinery, which is the strongest evidence the plan has produced that
the shape holds.

### Policies applied

- **`testing-conventions`** — the unit suite never launches a browser or
  reaches the network: `build_argv` and `summarise` are pure, and the plugin's
  graph is exercised with `run_program` stubbed. The real thing is requirement
  8's script.
- **CLAUDE.md, "a module that touches real I/O is its own file"** —
  `browse.py` is pure; the body does the I/O.
- **CLAUDE.md, "a caller-supplied string passed to a CLI tool as a bare
  positional"** — the task is a positional argument to `python -c`, after the
  script, so it lands in `sys.argv[1]` and can never be read as an option to
  `python`. There is no `--` to add because `-c` already terminates option
  parsing for what follows.
- **CLAUDE.md, "one capability, one plugin, one backend"** — one browser
  driver, called directly.
- **capability blueprint §4.2/§4.3** — Shape B, entered by goal.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| the model key | stored, `state_dir/.env` | `PLUGIN-CONFIG-01` owns it |
| the model id | a config key | a behaviour |
| the agent script | a constant | it is code, and code lives in the repository |
| whatever browser-use keeps | its own, outside this project | a browser profile and any recordings are browser-use's business; this project neither manages nor cleans them, and `intent.md` scoped no memory between runs |

Nothing new persists here. Note the last row honestly: browser-use has its own
state directory and this design does not touch it, which means the retention
question `RETENTION-01` raises has a second, larger instance nobody is
watching.

## Interface

**`plugin.toml`:** entry `web.browse`, parameters `schema/browse.json` —
`task` (string, required). One `[[setting]]`, `api_key`, secret.

**`src/sadana/browse.py`:**

- `AGENT_SCRIPT: str` — the fixed first-party script.
- `build_argv(task: str) -> tuple[str, ...]` — the full `uvx …` invocation.
- `summarise(ran: Ran) -> str` — what the model reads, defanged through
  `untrusted_text` because a page's contents reach it this way.
- Config: `SADANA_BROWSE_MODEL`, `SADANA_BROWSE_TIMEOUT_S` (default 300 —
  browsing is slower again than drawing).

**The node body** returns a `str` in every case.

## Acceptance criteria

- [ ] `validate()` on the shipped directory returns `Valid`.
- [ ] `build_argv` puts the task in its own element and never concatenates it
      into the script; a task containing quotes, a semicolon and a newline
      survives as one argument.
- [ ] The script constant contains no interpolation of anything
      caller-supplied.
- [ ] `summarise` defangs what it reports — a page's text reaches the model
      this way.
- [ ] With no key set, the run fails before the first node naming `api_key` —
      the `PLUGIN-CONFIG-01` preflight.
- [ ] A `run_graph` walk with `run_program` stubbed returns the summary; a
      stubbed `Failure` returns a plain sentence; a non-zero exit returns a
      plain sentence carrying what the program said.
- [ ] The plugin asks for a longer timeout than the program default.
- [ ] `scripts/prove_browse.py` drives a real browser to a real page and
      reports what it found; output in `review.md` § Evidence.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Keeping a browser open between calls, or any memory of earlier browsing.
- Letting the person intervene mid-task.
- Letting the model supply code. That is the Shape C the blueprint refuses and
  that `browser_exec` actually is.
- Managing, cleaning or bounding browser-use's own state directory.
- Installing browser-use or a browser. `uvx` fetches on first use; a browser is
  already present via playwright, and its absence is a plain failure sentence.
- Screenshots or any artifact. A later item, now that `ARTIFACT-STORE-01`
  exists.

## Open questions

**Should the agent's own model be the same one the conversation uses?** It is a
separate config key here, defaulting to something cheap, because browsing burns
many steps and each is a model call. Sharing the conversation's model would be
simpler and would make a browse cost several times a turn.

**Is `uvx` an acceptable dependency in practice?** It is not a Python
dependency, but it is a program that must exist, and it fetches from the
network on first use. On a machine without it the plugin cannot run. That is
the same class of requirement as needing a browser, and it is stated rather
than solved.

## Rejected alternatives

**Importing `browser_exec` as the blueprint scheduled.** It takes `code`. That
is Shape C, the model drives, and a plugin may not be driven (§4.3). This is
the finding of the item.

**Shipping the browser-use CLI's code-on-stdin interface instead.** Same
objection, plus it needs a stdin channel `SUBPROCESS-01` deliberately does not
have.

**Adding browser-use to this project's dependencies.** It brings a hundred-odd
packages; `uvx` keeps them in their own environment, which is what makes
requirement 3 satisfiable rather than aspirational.

**Generating the script from the task.** Any string-building around code is the
injection this design exists to avoid. The script is a constant.

**A `browser` backend seam.** One driver. §6 Rule 1.

## Concerns

**This is the least contained thing this project has ever run, and the
containment argument is entirely social.** browser-use decides its own actions,
reads pages nobody vetted, and runs with the person's access. `run_program`
says it is not a boundary, `intent.md` records the owner accepting that, and
between them that is the whole of it. Every other item in this plan could point
at a mechanism; this one points at a decision.

**The pages it reads go to a model, and that is a second exposure that is easy
to forget.** It is in the entry's `purpose` so the agent repeats it, and in
requirement 6 — but a person who has approved "browse the web for me" has not
obviously understood "and send everything you see to an inference provider".

**The blueprint was wrong and I nearly built to it.** The Shape B justification
for this item rested on a misreading of hermes's own tool, and the check that
caught it was reading `browser_use_cli.py` rather than trusting the blueprint's
citation of it. That is worth recording as a process finding: this plan's
design documents have been cited by section number for five work items, and
this is the first time one was verified against the code it describes.

**browser-use's own state is unmanaged and larger than ours.** It keeps a
browser profile and optionally recordings. `RETENTION-01` is written about the
files *this* project makes; this adds a bigger pile nobody is watching, outside
the directory that work item is about.
