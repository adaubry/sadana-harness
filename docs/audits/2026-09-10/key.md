# Audit key — DO NOT SHOW TO A PASS-ONE AUDITOR

Open this only at step 3 of audit-skill, after every pass-one report is in.

## A — CONFIG as a contract

Commits: ad5d185

**C (what the plan said):** Every part of the program needs to know things like "where do my files live" and "which model am I using". If each part works that out its own way, the same answer ends up written in five places and changing it means finding all five. We need one place that answers those questions — and we need it before anything starts asking.

## B — The conversation cycle, on paper

Commits: 4c2e954

**C (what the plan said):** Three pieces have to work together: the loop that runs a turn, the piece that calls the model, and the piece that manages what fits in the prompt. If we just start writing them, they end up calling into each other in every direction and none of them can be tested or changed on its own. So we write down who calls whom, on paper, before any of the three exists.

## C — MODEL-ACCESS v0

Commits: f81d52b

**C (what the plan said):** We can't do anything at all until we can send a message to a model and get an answer back. That's the whole problem. This is where that becomes possible — one provider, one way of talking to it, working for real.

## D — CONVERSATION

Commits: 116e59c, 7e4f71f, 48111aa, 65f02ee, bc39f17, 67f429e, 9181cb0, fbb1fb6, 7376775, c3603a3

**C (what the plan said):** One question and one answer is not an agent. An agent takes a turn: it thinks, it may ask to use a tool, it gets the result, it thinks again, and eventually it stops. Something has to run that cycle, decide when to stop, and keep the history straight. This is that something.

## E — Eval harness

Commits: 1634433

**C (what the plan said):** make verify tells us the code runs without crashing. It does not tell us the agent behaves sensibly. Those are two different questions, and we only had a way to ask the first one. This gives us a way to write down "when asked this, it should do that" and check it automatically.

## F — Checkpoint one real turn

Commits: a698311

**C (what the plan said):** We've built six things and never proved they work together against a real model. No new code here. The problem this solves is that we were about to keep building on top of something unverified.

## G — CONTEXT

Commits: 73ac4f0, fa5a63a

**C (what the plan said):** A model can only read so much at once, and a long conversation eventually doesn't fit. Something has to decide what to keep, what to summarise, and what to drop — without disturbing the parts of the prompt that are meant to stay byte-for-byte identical every turn.

## H — SESSION-STORE

Commits: f2962bb

**C (what the plan said):** A conversation only exists while the program is running. Close it and it's gone. If someone should be able to come back tomorrow and continue where they left off, the conversation has to be written down somewhere and read back.

## I — The plugin's static contract and it becomes visible

Commits: b1cdd7c, 46b1067, 6e3c3b3

**C (what the plan said):** When the agent uses a plugin today, all that comes back is a line of text. We can't tell which steps ran, whether it failed or succeeded, or where a file it made ended up — a failure and a success look the same. We need a real, structured answer instead of a sentence. And separately: a plugin is a folder on disk, and nothing yet reads that folder or checks it makes sense. Additionally, the agent has no idea which plugins exist. We have to tell it — "here are the things you can do" — and we need something that actually follows a plugin's steps in the right order when it's asked to. Until this, plugins are folders nobody looks at.

## J — Checkpoint smallest complete plugin

Commits: 6724977

**C (what the plan said):** Same idea as the earlier checkpoint — stop and prove it. The simplest possible plugin (one instruction file, one step) should work start to finish. If the simplest case doesn't work, nothing more complicated is going to.

## K — EXECUTION re-scoped

Commits: 14ccd04

**C (what the plan said):** A plugin step usually needs to do something real — call an API, read a file, talk to a database. So far nothing in our system is allowed to reach outside itself. We have to decide what a step may touch and where it runs. We deliberately waited until now, because you can't decide what a step is allowed to do before you know what a step is.

## L — SAFETY one gate

Commits: 8d31042

**C (what the plan said):** The moment a plugin can do real things, it can do the wrong real thing — send an email, delete a file, spend money. A person needs a chance to say yes first. We do it now rather than later because adding permission to a system that already runs without it means touching everything again, tangled.

## M — PLUGINS execution half

Commits: 66fcfce, 33e4d1e

**C (what the plan said):** We now have permission to do real things and a way to ask first. This is where plugins actually start doing them — calling APIs, producing files and links. It's also where we throw away our hand-written test script and replace it with real plugins, so the thing we've been pretending to have becomes the thing we have.

## N — Checkpoint real plugin under approval

Commits: f8900f2

**C (what the plan said):** End of the first phase. Everything so far has been building the base. The problem is that nobody has checked the base holds. A real multi-step plugin runs from start to finish, asks permission where it should, and produces something. If that works, the foundation is finished.

## O — CLI-SHELL

Commits: 769a1ae, c4d47fe, 2baff88, 4f72266, 7c29e5a, f47862e, 1a659a3, 4a3a830

**C (what the plan said):** There is no way to just use this. The only way in is running a test or a script. We need a command you can type. This is also where "show me my past conversations" and "find the one from last week" finally get added — we skipped them earlier because nothing existed that could have shown them to you.
