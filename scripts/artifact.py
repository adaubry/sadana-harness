#!/usr/bin/env python3
"""artifact — the six-step state machine for one unit of work.

One artifact = one work item = one commit. It moves through six stages, and
each stage is *done* only when its file exists, has every required section,
and carries the trace its methodology is supposed to leave behind.

    scripts/artifact.py new A1 config-contract   start a work item
    scripts/artifact.py status                   where am I, what is missing
    scripts/artifact.py check                    validate — exits non-zero
    scripts/artifact.py approve                  write 'approved' — run this yourself, never an agent
    scripts/artifact.py gate <write|edit|commit> <path>   used by the hook

The design decision worth understanding:

    We do not check that a skill was loaded. We check that the artifact
    carries evidence the methodology produced it. A skill can be loaded and
    ignored; a "Challenged and changed" section cannot be filled in by a
    session that never ran the interview.

Approval is the second thing it checks, and it is not the same thing. A
valid artifact is one the methodology produced; an approved artifact is one a
person read and agreed to. That word lives on the `Author: ... Status: ...`
line, a person writes it, and no agent ever does. A plan's `## Files that
change` is the other half: it is not documentation, it is the list of files
the approval covers, and a write outside it is refused.

Stage definitions live in STAGES below. Edit them; that is the tuning surface.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

TASKS = Path("docs/tasks")
ACTIVE = Path(".claude/active-task")
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b")
# The approval word, and every path-shaped token a plan can claim.
STATUS_RE = re.compile(r"^Author:.*?Status:\s*([^\s.,;]+)", re.M)
# Two segments minimum, deliberately. A one-segment form (`src/`, `tests/`)
# is indistinguishable from the prose every plan writes about those
# directories, and accepting it hands a plan the whole tree because it
# mentioned it. A lane that wants a directory writes the glob: `src/*`.
PATH_TOKEN = re.compile(r"[\w.*?\[\]-]+(?:/[\w.*?\[\]-]+)+/?")
# One grammar, deliberately: the rule that admits the header line is the same
# expression status_of() parses it with, so "valid" cannot mean "unreadable".
AUTHOR_RULE = (
    STATUS_RE.pattern,
    "needs an `Author: ... Status: ...` line — the status word is where approval is recorded",
)
# The build stage's ownership section, named once: planned_paths() and the
# stage table below must agree or every source write is refused.
OWNERSHIP_SECTION = "Files that change"


@dataclass
class Stage:
    key: str
    artifact: str
    skill: str
    sections: list[str]  # must be present and non-empty
    # The trace: a section that only gets filled if the methodology actually
    # ran. This is what separates "the file exists" from "the process ran".
    trace: str
    trace_min_words: int = 10
    optional_sections: list[str] = field(default_factory=list)  # may be empty
    header_rules: list[tuple[str, str]] = field(default_factory=list)  # (regex, why)
    warn_rules: list[tuple[str, str]] = field(default_factory=list)  # advisory if present
    warn_absent_rules: list[tuple[str, str]] = field(default_factory=list)  # advisory if missing
    produces_code: bool = False
    needs_approval: bool = True


STAGES = [
    # Plan follows Anthropic's intent.md template verbatim. The five sections
    # below ARE the template; `Changed during planning` is this project's own
    # trailing addition, and it is the only thing that lets a validator tell an
    # interviewed intent from a dictated one.
    Stage(
        "plan",
        "intent.md",
        "plan-skill",
        sections=["Problem", "Proposed outcome", "Affected users and systems", "Constraints"],
        optional_sections=["Open questions"],
        trace="Changed during planning",
        header_rules=[
            (r"^#\s*Intent:\s*\S", "first line must be `# Intent: <short name>`"),
            AUTHOR_RULE,
        ],
        warn_rules=[
            (
                r"`|```|\bdef \b|\bclass \b|/[a-z_]+\.py",
                "reads like design, not intent — module names and code belong in spec.md",
            ),
        ],
    ),
    # Design answers Anthropic's brief: a requirements AND design spec, ready to
    # hand over, with concerns stated — "especially where you cannot satisfy
    # contradicting policies". Concerns is the trace: a designer who did not
    # think hard has none to report.
    Stage(
        "design",
        "spec.md",
        "design-skill",
        sections=["Requirements", "Design", "Interface", "Acceptance criteria", "Rejected alternatives"],
        optional_sections=["Non-goals", "Open questions"],
        trace="Concerns",
        trace_min_words=12,
        header_rules=[
            (r"^#\s*Spec:\s*\S", "first line must be `# Spec: <short name>`"),
            (
                r"^Intent:\s*\S",
                "needs an `Intent: <path>` line — a spec that cannot name its intent " "is not traceable",
            ),
            AUTHOR_RULE,
        ],
        warn_absent_rules=[
            (
                r"reference|hermes|prior art",
                "no reference-corpus consultation recorded — design guideline 1 says "
                "learn from prior art before proposing; say so explicitly if none applies",
            ),
            (r"^\s*[-*]\s*\[[ x]\]", "Acceptance criteria read better as a checklist a second person can tick"),
        ],
    ),
    # Build follows the user's plan.md template. Risks is the trace: it is what
    # the interrogation produces, and a plan nobody questioned has thin risks.
    # "What options were previously avoided" is NOT re-recorded here — it lives
    # in spec.md's Rejected alternatives, and the interrogation checks the plan
    # has not drifted back toward one of them.
    Stage(
        "build",
        "plan.md",
        "build-skill",
        sections=[OWNERSHIP_SECTION, "Order of work", "Proof"],
        optional_sections=["Open questions"],
        trace="Risks",
        trace_min_words=15,
        produces_code=True,
        header_rules=[
            (r"^#\s*Plan:\s*\S", "first line must be `# Plan: <short name> (from intent.md <date>)`"),
            (r"from\s+intent\.md", "the header must cite the intent this plan descends from"),
            AUTHOR_RULE,
        ],
        warn_absent_rules=[
            (r"^\s*\d+\.\s+\S", "Order of work reads better as a numbered sequence"),
        ],
    ),
    # The last stage that commits a file. review.md is this repo's stand-in for
    # the PR: the playbook attaches verification evidence to the PR check run
    # so that "review can concentrate on intent and risk because the mechanical
    # evidence is already attached". Until there is CI, it attaches here.
    # Findings stays the trace — a review nobody ran has none.
    Stage(
        "deploy",
        "review.md",
        "deploy-skill",
        sections=["Evidence", "Findings", "Decision"],
        trace="Findings",
        trace_min_words=6,
        # Nothing reads review.md's status: it is the last artifact, and the
        # decision it carries is the approval, not a word on its header line.
        needs_approval=False,
        header_rules=[
            (r"```", "## Evidence must carry pasted `make verify` output, not a claim"),
        ],
    ),
]
BY_ARTIFACT = {s.artifact: s for s in STAGES}


# ── parsing ────────────────────────────────────────────────────────────────
def sections_of(text: str) -> dict[str, str]:
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def status_of(path: Path) -> str:
    """The approval word on an artifact's `Author: ... Status: ...` line.

    Lowercased, trailing punctuation dropped. "" when the file or the line is
    missing, which reads as "not approved" everywhere this is used.
    """
    if not path.exists():
        return ""
    m = STATUS_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1).lower() if m else ""


def planned_paths(text: str) -> set[str]:
    """Every path a plan.md's `## Files that change` section claims.

    A token scan, not a layout parser. The plans already in this tree use four
    incompatible formats — a comma-separated line, `-` bullets whose prose
    wraps onto continuation lines carrying further paths, and two aligned-
    column styles — and a parser that understands three of them refuses the
    fourth. A wrong refusal teaches people to route around the gate, which is
    worse than a claim that is too generous; the section always was a list of
    claims, and this reads it as one.

    Anything containing a `/` is a claim. `(new)`/`(edit)`/`(delete)`
    annotations carry none and fall out by construction. A trailing `/` means
    a directory, so it becomes a glob over what is inside it.
    """
    secs = {norm(k): v for k, v in sections_of(text).items()}
    body = secs.get(norm(OWNERSHIP_SECTION), "").replace("`", " ")
    found = {m.group(0).removeprefix("./") for m in PATH_TOKEN.finditer(body)}
    return {p + "*" if p.endswith("/") else p for p in found}


def validate(path: Path, stage: Stage) -> list[str]:
    """Blocking problems only. Empty list == this stage is done."""
    return _inspect(path, stage)[0]


def _inspect(path: Path, stage: Stage) -> tuple[list[str], list[str]]:
    """Return (problems, warnings). Warnings never block; they are advice."""
    if not path.exists():
        return [f"{path} does not exist"], []
    text = path.read_text(encoding="utf-8")
    secs = {norm(k): v for k, v in sections_of(text).items()}
    problems: list[str] = []
    warnings: list[str] = []

    for rx, why in stage.header_rules:
        if not re.search(rx, text, re.M):
            problems.append(why)

    for want in stage.sections:
        key = norm(want)
        if key not in secs:
            problems.append(f"missing section '## {want}'")
        elif not secs[key]:
            problems.append(f"section '## {want}' is empty")
        elif PLACEHOLDER.search(secs[key]):
            problems.append(f"section '## {want}' still has a TODO/TBD placeholder")

    # Optional sections may be absent or say "none" — an intent with nothing
    # unresolved is a real outcome, not a gap to punish.
    for want in stage.optional_sections:
        key = norm(want)
        if key in secs and PLACEHOLDER.search(secs[key]):
            problems.append(f"section '## {want}' still has a TODO/TBD placeholder")

    tkey = norm(stage.trace)
    if tkey not in secs:
        problems.append(
            f"missing '## {stage.trace}' — this section is the evidence the "
            f"{stage.skill} methodology ran, not just that the file was written"
        )
    else:
        words = len(secs[tkey].split())
        if words < stage.trace_min_words:
            problems.append(f"'## {stage.trace}' has {words} words, needs {stage.trace_min_words}+")

    for rx, why in stage.warn_rules:
        if re.search(rx, text, re.M):
            warnings.append(why)
    for rx, why in stage.warn_absent_rules:
        if not re.search(rx, text, re.M | re.I):
            warnings.append(why)
    return problems, warnings


# ── task discovery ─────────────────────────────────────────────────────────
def task_dir(tid: str | None) -> Path | None:
    if tid:
        hits = sorted(TASKS.glob(f"{tid}-*")) + ([TASKS / tid] if (TASKS / tid).is_dir() else [])
        return hits[0] if hits else None
    if ACTIVE.exists():
        p = Path(ACTIVE.read_text().strip())
        return p if p.is_dir() else None
    return None


def stage_state(d: Path) -> list[tuple[Stage, list[str]]]:
    return [(s, validate(d / s.artifact, s)) for s in STAGES]


# ── commands ───────────────────────────────────────────────────────────────
def cmd_new(tid: str, slug: str) -> int:
    d = TASKS / f"{tid}-{slug}"
    if d.exists():
        print(f"{d} already exists", file=sys.stderr)
        return 1
    d.mkdir(parents=True)
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE.write_text(str(d))
    print(f"▶ {d}")
    print(f"▶ active task set. Next stage: plan → {d}/intent.md")
    print("▶ Load /plan-skill and let it interview you. Do not write intent.md yourself.")
    return 0


def cmd_status(tid: str | None) -> int:
    d = task_dir(tid)
    if not d:
        print("no active task. `artifact.py new <ID> <slug>` to start one.")
        return 0
    print(f"task: {d}")
    nxt, waiting = None, []
    for s, probs in stage_state(d):
        if not (d / s.artifact).exists():
            mark, detail = "·", "not started"
            nxt = nxt or s
        elif probs:
            mark, detail = "✗", f"{len(probs)} problem(s)"
            nxt = nxt or s
        elif s.needs_approval and status_of(d / s.artifact) != "approved":
            mark, detail = "◦", "valid, awaiting approval"
            waiting.append(s.artifact)
        else:
            mark, detail = "✓", "done"
        print(f"  {mark} {s.key:9s} {s.artifact:18s} {detail}")
        if probs and (d / s.artifact).exists():
            for p in probs:
                print(f"      - {p}")
    print()
    if waiting:
        print(f"waiting on the user: {', '.join(waiting)} — nobody else writes 'approved'.")
    if nxt:
        print(f"next: {nxt.key} — methodology: {nxt.skill} — produce {nxt.artifact}")
    elif not waiting:
        print("all stages complete. Ready to commit.")
    return 0


def cmd_check(tid: str | None) -> int:
    d = task_dir(tid)
    if not d:
        # No work item in flight is a valid state, not a failure. `make chain`
        # has to be green on a clean tree or the whole gate deadlocks.
        print("no active task — nothing to check")
        return 0
    bad = 0
    for s, probs in stage_state(d):
        if not (d / s.artifact).exists():
            continue  # not started is not a failure; incomplete is
        _, warns = _inspect(d / s.artifact, s)
        for p in probs:
            print(f"{d/s.artifact}: {p}", file=sys.stderr)
            bad += 1
        for w in warns:
            print(f"{d/s.artifact}: note — {w}")
    if bad:
        print(
            f"\n{bad} problem(s). The methodology for a stage has not been "
            f"completed, whatever the transcript says.",
            file=sys.stderr,
        )
        return 1
    print(f"{d}: all present artifacts valid")
    return 0


def cmd_approve(tid: str | None) -> int:
    """Write 'approved' on the one valid artifact still waiting for it.

    This is the user's own act, not an agent's — CLAUDE.md forbids an agent
    from writing the word, so this command exists to be typed by a person,
    with a `!`-prefix in Claude Code or directly in a shell, never run on the
    user's behalf. It edits only the captured status word inside the
    `Author: ... Status: ...` line, so — unlike a blanket sed — it cannot
    touch prose elsewhere in the file that happens to contain the same text.
    """
    d = task_dir(tid)
    if not d:
        print("no active task.", file=sys.stderr)
        return 1
    waiting = []
    for s, probs in stage_state(d):
        path = d / s.artifact
        if not path.exists():
            continue
        if probs:
            print(f"Cannot approve {path}: {len(probs)} problem(s) remain. Fix those first.", file=sys.stderr)
            return 1
        if s.needs_approval and status_of(path) != "approved":
            waiting.append(path)
    if not waiting:
        print("nothing is awaiting approval.")
        return 0
    if len(waiting) > 1:
        print("more than one artifact is awaiting approval:", file=sys.stderr)
        for p in waiting:
            print(f"  - {p}", file=sys.stderr)
        print("approve the earlier one first, or edit its Status line by hand.", file=sys.stderr)
        return 1
    path = waiting[0]
    text = path.read_text(encoding="utf-8")
    m = STATUS_RE.search(text)
    if not m:
        print(f"{path}: no Author/Status line found.", file=sys.stderr)
        return 1
    start, end = m.span(1)
    path.write_text(text[:start] + "approved" + text[end:], encoding="utf-8")
    print(f"{path}: Status → approved.")
    return 0


def _refuse_unapproved(path: Path, prefix: str = "") -> int:
    """Said identically by all three branches, because it is one rule."""
    st = status_of(path) or "missing"
    print(f"Blocked: {prefix}{path} is not approved.", file=sys.stderr)
    print(f"  Status is '{st}'; the user writes 'approved' after reading it.", file=sys.stderr)
    print("  Do not write that word yourself.", file=sys.stderr)
    return 2


def cmd_gate(action: str, target: str) -> int:
    """Answer for the hook. 0 = allow, 2 = block with reason on stderr."""
    d = task_dir(None)
    rel = target.replace("\\", "/").removeprefix("./")
    if rel.startswith("/"):
        with suppress(ValueError):
            rel = Path(rel).resolve().relative_to(Path.cwd().resolve()).as_posix()

    # Writing an artifact: every EARLIER stage must be complete.
    name = Path(rel).name
    if name in BY_ARTIFACT and "docs/tasks/" in rel:
        stage = BY_ARTIFACT[name]
        owner = Path(rel).parent
        idx = STAGES.index(stage)
        for prev in STAGES[:idx]:
            probs = validate(owner / prev.artifact, prev)
            if probs:
                print(
                    f"Blocked: {name} belongs to the '{stage.key}' stage, but " f"'{prev.key}' is not finished.",
                    file=sys.stderr,
                )
                for p in probs[:4]:
                    print(f"  - {p}", file=sys.stderr)
                print(f"Finish {prev.artifact} first (methodology: {prev.skill}).", file=sys.stderr)
                return 2
            if status_of(owner / prev.artifact) != "approved":
                return _refuse_unapproved(owner / prev.artifact)
        return 0

    # Touching source: the build stage's plan must exist and be valid.
    if action in ("write", "edit") and re.match(r"^(src|tests)/", rel):
        if not d:
            print(
                "Blocked: no active task. Source changes belong to a work item.\n"
                "Run `scripts/artifact.py new <ID> <slug>` and go through plan → "
                "design → build first.",
                file=sys.stderr,
            )
            return 2
        build = BY_ARTIFACT["plan.md"]
        probs = validate(d / build.artifact, build)
        if probs:
            print(
                f"Blocked: no valid {d}/plan.md, so this edit is not covered by an " f"approved plan.", file=sys.stderr
            )
            for p in probs[:4]:
                print(f"  - {p}", file=sys.stderr)
            print(f"Produce it with /{build.skill} (plan mode), then retry.", file=sys.stderr)
            return 2
        plan_path = d / build.artifact
        if status_of(plan_path) != "approved":
            return _refuse_unapproved(plan_path)
        if not any(fnmatch.fnmatch(rel, e) for e in planned_paths(plan_path.read_text(encoding="utf-8"))):
            print(f"Blocked: {plan_path} does not name {rel} under '## {OWNERSHIP_SECTION}'.", file=sys.stderr)
            print(
                "Add it there and tell the user you did — the approval was for the old list. Or stop.",
                file=sys.stderr,
            )
            return 2
        return 0

    # Committing: everything up to and including test must be valid.
    if action == "commit":
        if not d:
            return 0
        for s in STAGES:
            if s.key == "deploy":
                break
            probs = validate(d / s.artifact, s)
            if probs:
                print(f"Blocked: cannot commit — stage '{s.key}' is incomplete.", file=sys.stderr)
                for p in probs[:4]:
                    print(f"  - {p}", file=sys.stderr)
                print("One artifact, one commit. The chain has to be whole.", file=sys.stderr)
                return 2
            if s.needs_approval and status_of(d / s.artifact) != "approved":
                return _refuse_unapproved(d / s.artifact, "cannot commit — ")
        return 0
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 0
    c = argv[1]
    if c == "new" and len(argv) == 4:
        return cmd_new(argv[2], argv[3])
    if c == "status":
        return cmd_status(argv[2] if len(argv) > 2 else None)
    if c == "check":
        return cmd_check(argv[2] if len(argv) > 2 else None)
    if c == "approve":
        return cmd_approve(argv[2] if len(argv) > 2 else None)
    if c == "gate" and len(argv) == 4:
        return cmd_gate(argv[2], argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
