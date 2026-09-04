# sadana — EVAL HARNESS & CI: intent material and architecture spec

Reference audited: `hermes-agent@29112bef09` (workflows, `evals/readtool`, `evals/browser_use`), plus one unmerged side-branch commit `5bbb4cd6a9` for `evals/core_tool_deferral` (that directory does not exist at hermes's checked-out HEAD; every claim about it below is cited against `5bbb4cd6a9`, not the working tree).

This is not one of the 21 L1 blocks in `docs/reference/hermes_core_blocks_kind.csv` — no block there is dedicated to evals or CI. It sits across `CONVERSATION`/`MODEL-ACCESS`/`SESSION-STORE` (which tag `evals/*` files by what they exercise) and `.github/workflows/` (untagged, outside the CSV entirely). This document exists because the same methodology — audit the reference before designing — applies regardless of whether the thing being designed maps onto one of the 21 blocks.

Like the CONVERSATION blueprint, this is not an `intent.md`. It is the material for the interview that produces one, plus the architecture decisions that should survive into `spec.md`.

---

## 0. Starting position

`make verify` (chain → lint → typecheck → test) proves code executes correctly. Nothing proves an agent's *behavior* is correct — whether it calls the right tool, defers to a plugin instead of doing something itself, stays within the guarantees CONV-01 through CONV-10 just built and proved once, by hand, against a real model. There is no CI at all yet (`.github/` does not exist in this repo); every check today depends on a human running `make verify` locally before asking for review.

Two separate but related gaps, both raised by the user in one message:

1. **No CI.** `make verify` is real and sufficient; it just isn't automatic.
2. **No eval harness.** Even if CI ran `make verify` on every push, that would only prove the *code* still runs — not that the *agent* still behaves. Every capability added after this point is unfalsifiable without something that drives the real conversation loop against a real model and checks what it actually did.

## 1. What the reference corpus actually has — and doesn't

### 1.1 CI

hermes's `.github/workflows/` is large (27+ files) and almost all of it is scale-specific to a much bigger, more mature product (docker builds, docs-site deploys, installer e2e, JS/desktop e2e, contributor-check, supply-chain scanning). The one structural idea worth taking at any scale: `ci.yaml` gates a change-detector job feeding N parallel job lanes into one aggregating `all-checks-pass` job (`ci.yaml:216-268`) that branch protection points at — `skipped` counts as pass, only `failure` fails it, so adding or removing a lane never means reconfiguring branch protection. `tests.yml` blanks real provider credentials in the CI environment (`OPENROUTER_API_KEY: ""`, `tests.yml:124-126`) to structurally guarantee no live network call happens under CI, the same hermetic posture `scripts/run_tests.sh` already gives this project locally.

`history-check.yml`'s name suggested regression tracking; it is not that — it is a guard against a specific incident (an orphaned branch with no common ancestor to `main` collapsing `git blame`, `history-check.yml:5-11`). Confirmed not applicable to what this document is trying to solve.

**The single most load-bearing finding:** `grep -rl "evals/" .github/workflows/` returns nothing. hermes never wires its own eval suite into CI, on any trigger. Despite having the mature harness described below, hermes's own answer to "should this run on every PR" is no.

### 1.2 Eval harness

Three domains read in full: `evals/core_tool_deferral` (closest analog to this project's own plugin-dispatch concern — does the agent defer to a tool instead of doing the work itself), `evals/readtool`, `evals/browser_use`. All three share a shape worth naming precisely, because it is not what "eval" often means elsewhere (no LLM-as-judge anywhere in any of the three):

- **A task is data**: a prompt (or fixture-building function) plus a programmatic grader function — never a single expected string. `core_tool_deferral`'s grader inspects tool-call counts, a callback log, and workspace file contents and returns partial credit (`tasks.py:76-124`); `readtool`'s grades the agent's final text via regex/substring checks against a fixed, seeded-RNG fixture whose ground truth is planted ahead of time (`fixtures.py:39-56`); `browser_use`'s is a plain regex oracle match (`single_run.py:169-176`).
- **The driver is the real agent**, not a scripted stand-in — same posture this project already committed to with `scripts/prove_conversation_e2e.py`. hermes runs its actual `AIAgent`/`run_conversation` against a real model, spending real money, exactly the same trade this project already made once.
- **Results are resume-safe, per-cell JSON**, deduped by `(arm, task, model, rep)` so an interrupted battery doesn't repay for cells it already has (`orchestrator.py:37-52`, `orchestrate.py:44-50`).
- **Regression detection is a comparative report, not an automated gate.** `report.py --labels baseline feat-x` diffs two labeled result sets side by side with percentage deltas (`readtool/report.py:59-108`) — this is an A-vs-B mechanism (this run vs that run), not historical N-vs-N-1 drift tracking, and it produces a table a human reads. Every domain's actual verdict lives in a hand-written `results/SUMMARY.md` ("SHIP, with one follow-up," `core_tool_deferral/results/SUMMARY.md:64-76`) — there is no code anywhere that fails a build on a score dropping.
- **No automated pass-rate threshold exists in any of the three domains.** "Deltas within ±3% are noise" is a documented human heuristic (`readtool/README.md:52`), not an enforced check.
- **Cost is real and on-demand.** Every domain is invoked by hand for a specific comparison (a PR's before/after, a feature's baseline-vs-new), never on a schedule, never in CI (confirmed by 1.1's grep).
- **The same-denominator rule**: an errored task run scores 0 and stays in the accuracy denominator (excluded only from efficiency-specific means) — a failure is never silently dropped from the count (`core_tool_deferral` methodology, cited in the research report backing this document).

## 2. What this means for the fork the user raised

The user's own framing — "unfalsifiable... nothing to stop it drifting" — reads as wanting an automated regression gate. hermes, at 27,000 commits of production maturity, does not have one. Its answer to drift is: a comparative report plus a human reading it, the same posture this project's own Deploy stage already takes for code (`review.md`'s `## Decision`, never automated). This is worth surfacing plainly rather than silently deciding it in either direction:

- **Match hermes's posture**: build the harness and the comparative report; a human reads it and decides, the same way `review.md` decisions already work. Cheapest, proven at hermes's own scale, and consistent with this project's whole SDLC philosophy (a person governs, the loop executes).
- **Go further than hermes**: add an automated threshold (e.g. "task pass rate must not drop more than N points vs the last recorded baseline, or CI fails") — solves a scenario hermes itself apparently never needed solved, or never got around to. A real bet, not a small one: false positives from LLM non-determinism would need real handling (rep counts, noise bands) that hermes's own `README.md:52` heuristic suggests they never fully automated either.

This is an intent-stage decision, not a design-stage one — named here, not resolved here.

## 3. Architecture — first pass

```
┌─────────────────────────┐
CI (new)                 │  triggers make verify on push/PR;
  one job → one gate      │  never triggers the eval harness
└─────────────────────────┘

┌─────────────────────────────────────────────────┐
EVAL HARNESS (new)                                │
  Task = (prompt | fixture_fn, grader_fn)          │  data, not code-per-task
  Runner: real create_conversation()/take_turn()   │  same posture as
          against a real model, real spend          │  prove_conversation_e2e.py
  Result: resume-safe JSON per (task, model, rep)  │
  Report: comparative A-vs-B table                  │  human-read verdict,
                                                     │  not an automated gate
                                                     │  (pending the fork above)
└─────────────────────────────────────────────────┘
```

Proposed layout (adapt at design stage):

```
.github/workflows/ci.yaml       triggers make verify, one gate job
scripts/eval/
    tasks/                       task definitions (data)
    runner.py                    drives real conversation loop against real model
    report.py                   comparative A-vs-B diff
    results/                    resume-safe per-cell JSON (gitignored, like hermes's own evals/*/results/.gitignore)
```

Not `tests/` — these are real-money, non-deterministic, on-demand runs, explicitly outside `make test`'s hermetic suite, the same boundary `prove_conversation_e2e.py` already established and this project's own `testing-conventions` skill already enforces.

## 4. Work items, proposed build order

Each line is one artifact chain, one commit — mirroring the CONVERSATION blueprint's own §7.

1. **CI-01: `make verify` in CI.** The smallest real thing: one workflow, triggered on PR/push, running `make verify`, one gate job for branch protection, real credentials blanked in the CI env. No eval harness involved. Closes gap 1 on its own, independent of everything below.
2. **EVAL-01: task format + runner core.** The `Task` shape (prompt/fixture fn + grader fn, per §1.2), a runner that drives it through the real `create_conversation()`/`take_turn()` machinery this project already has, resume-safe JSON result storage. No tasks yet — proves the harness itself works, the same "no callers yet, deliberately" posture CONV-01 through CONV-05 used.
3. **EVAL-02: comparative report.** `report.py`-equivalent, A-vs-B labeled diff. Whether this stays purely informational or gets an automated threshold is the §2 fork — settled in EVAL-02's own intent.md, not assumed here.
4. **EVAL-03: first real task(s) against the CONVERSATION block.** Now that a harness exists, give it something to check — most naturally, does the model correctly choose between two mounted plugins when asked (the exact scenario `prove_conversation_e2e.py` already drives by hand, turned into a repeatable, graded task instead of a one-off script with inline asserts).

CI-01 has no dependency on EVAL-01/02/03 and could ship first or in parallel — named as an open question below, not decided here.

## 5. Open questions for intent.md (with my recommendation)

1. **Automated regression gate, or human-read comparative report (§2)?** Recommendation: match hermes's own posture at first (human-read, via EVAL-02's report) — it's the smaller bet, and this project's SDLC already puts a human at every decision point. Revisit once a real drift incident actually happens, the same "add a CLAUDE.md rule on the second occurrence" bar this project already uses elsewhere.
2. **Does CI-01 depend on EVAL-01, or ship independently?** Recommendation: independently, first. It's a complete, valuable, and much smaller piece of work, and nothing about it requires the harness to exist.
3. **Which model for the eval harness's real runs?** Recommendation: same as every other real-round-trip proof this project has produced — `deepseek/deepseek-v4-flash-0731` on `openrouter` — unless a specific task's grading needs a different capability profile, decided per-task, not project-wide.
4. **Where do eval tasks live relative to the CONVERSATION block's own fixture plugins** (`tests/fixtures/plugins/`, built for CONV-09)? Recommendation: EVAL-03's own scope question — likely reusable, decide once EVAL-01's task format is fixed.

## 6. Risks

- **Real spend, again.** Every eval run costs real API credit, same as `prove_conversation_e2e.py` and every provider proof before it. Same mitigation: local, no-network smoke-checks prove the harness's own mechanics before a real run is asked for.
- **LLM non-determinism makes "regression" genuinely ambiguous.** hermes's own unresolved posture (a human heuristic, not an automated band) is itself evidence this is a real, unsolved-even-at-scale problem — worth naming as a risk, not assuming away with an eval-count constant picked without evidence.
- **Temptation to build the automated-gate version because it sounds more rigorous.** hermes's own 27,000 commits didn't arrive there. Building past what hermes itself needed is exactly the kind of bet guideline 2 (reduce the number of bets) warns against — a real option, not a default.
