---
name: design-skill
description: Run the Design stage for a work item — turn an approved intent.md into a requirements and design spec, checked against every policy skill, and write spec.md. Use when a work item's intent is complete and spec.md is missing or incomplete, or when the user asks for a spec, requirements, or a design for an existing intent.
---

# Design stage — from intent to a spec engineering can build

Read `intent.md`. Produce a requirements and design spec for integrating it
into this codebase. Apply every policy skill available so the design conforms
to them. Write it as `spec.md`, ready to hand over.

**State your concerns plainly — above all where two policies cannot both be
satisfied.** A design that reports no tension either had none or was not
examined. Say which.

Design is where alternatives get argued. `intent.md` deliberately named no
module, file or pattern; this is the stage that names them, and the stage a
reader can disagree with.

## Procedure

1. `scripts/artifact.py status` — confirm the work item and that plan is done.
2. Read its `intent.md`. Every requirement you write must trace to something
   in it. If you find yourself needing a requirement the intent does not
   support, stop — that is scope creep, and it belongs in a new work item or
   an amended intent.
3. **Consult the reference corpus** (guideline 1 below). Do this before
   proposing anything.
4. Load every policy skill that applies and check the design against each.
5. Work the four design guidelines.
6. Write `docs/tasks/<ID>-<slug>/spec.md`.
7. `scripts/artifact.py check`. Fix what it reports.

## Policy conformance

Load and apply each of these that exists and is relevant:

- `project-structure` — where the new code lives
- `testing-conventions` — what "tested" will mean for this work item
- `reference-lookup` — how to consult prior art
- any security, brand or UX skill present in `.claude/skills/`

Two rules about policies:

- **Name the ones you applied**, in `## Design` or `## Concerns`. A policy you
  did not name is a policy the reviewer cannot check you against.
- **When two policies conflict, do not silently pick one.** Write both, write
  the tension, and say which you followed and at what cost. That is the single
  most valuable paragraph in the document.

If no policy skill applies to this work item, say that explicitly rather than
leaving the question unaddressed.

## The four design guidelines

These are this project's design position. Work all four; they interact.

### 1. Learn from the reference before proposing

The reference corpus has years of production behind it. For any block this
work item touches, find out how they did it before deciding how you will.
Use `reference-lookup`; filter to `kind == production-code`.

Record what you found and whether you are adopting or declining it. The bar
for declining is that you can name what is specifically wrong with it — not
that you would have done it differently. "They resolve config in three
directories and it cost them a partitionable core" is a reason. "I prefer a
dataclass" is not.

If nothing in the corpus is analogous, say so. Silence reads as not looking.

### 2. Reduce the number of bets

There is a fixed cost of change with a variable consequence. Every design
decision is a bet: the cost of reversing it later is roughly constant, but
what it costs you _varies enormously_ depending on how much came to depend on
it. So minimise the **count** of decisions you are betting on, not the size of
any one of them.

In practice: prefer a design where future value arrives as an **addition**
rather than a modification. Growth should mean _more plugins_, not more core
behaviour — empowering the agent one plugin at a time rather than solving for
every possibility now.

**The caveat matters more than the rule.** This only applies once the
infrastructure actually supports plugins. Before that seam exists, the
guideline becomes narrower: _do not foreclose it_. Avoid a decision that would
make the seam impossible or expensive to add later. Do **not** invent a plugin
system early in order to satisfy the rule — speculative infrastructure with no
consumer is exactly the failure the reference corpus warns about, and it is
also a bet, which is what you are trying to reduce.

Check where this work item sits relative to the plugin seam before applying
this, and say which case you are in.

### 3. Catch the scenario at the least step-cost

A working system is a set of steps that catch scenarios. What we call a
"problem" or a "danger" the system just sees as a scenario it does not catch.

When a scenario is uncaught there are only three moves:

- **add a step**
- **make a step heavier**
- **make a step harder**

Simplicity is not fewer features. It is catching the new scenario, _on top of
every scenario already caught_, with the least of those three.

So for the central choice in this design, state in `## Design` or
`## Rejected alternatives`: **which of the three moves does it make, and is
there a cheaper one?** If the design adds a step, ask whether an existing step
could have absorbed it. If it makes a step heavier, ask what that weight costs
every future caller. Answer it explicitly — this is the guideline most easily
satisfied in appearance and skipped in substance.

### 4. Minimise mutable state

Most things need some state. The question is which state is _truly necessary
to store_ versus what can be derived on the fly.

Inventory the state this design introduces. For each item: why can it not be
derived? Stored state is a bet (guideline 2) — it has a shape, a lifetime, a
migration story and a way of going stale. Derived state has none of those.

Minimising mutable state gets you further than almost any other single choice,
so make the inventory explicit rather than implicit.

## Writing the file

```markdown
# Spec: <short name>

Intent: docs/tasks/<ID>-<slug>/intent.md

## Requirements (numbered, each traceable to the intent)

## Design (how it integrates here — names files, modules, patterns)

## Interface (what crosses the boundary: inputs, outputs, errors)

## Acceptance criteria (a checklist a second person can tick)

## Non-goals (optional)

## Open questions (optional — omit if none)

## Rejected alternatives

## Concerns
```

`## Rejected alternatives` is where guidelines 1–4 land: the reference
approach you declined and why, the bet you chose not to take, the cheaper
step-move you considered, the state you decided to derive instead of store.

`## Concerns` is the trace. Anthropic's brief asks for areas of concern
_especially where contradicting policies cannot both be satisfied_. Write:
unresolved tensions, policies in conflict, anything you are uneasy about,
anything a reviewer should look at hardest. Twelve words minimum.

If you genuinely have none, say so **and say why you are confident** — a small
interface, no applicable policy, a decision that is cheap to reverse. "None"
alone fails, and it should.

## Do not

- Write requirements the intent does not support.
- Name a policy skill without having applied it.
- Pick one side of a policy conflict silently.
- Design a plugin system that has no consumer yet.
- Store state you could derive.
- Start writing code. Separate stage, separate skill — the chain gate will
  stop you, and it should.
