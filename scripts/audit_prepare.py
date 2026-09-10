#!/usr/bin/env python3
"""Prepare a full-repo audit: split the block list into blind assignments.

    python3 scripts/audit_prepare.py docs/audits/<YYYY-MM-DD>/blocks.md

Reads one line per block:

    <sha>[;<sha>;...] — <block name>: <the problem it was meant to solve>

Writes, next to it:

    assignments/<letter>.md   what a pass-one auditor sees: a letter, SHAs. No name.
    key.md                    letter -> block name -> problem. YOU ONLY.
    pass-two.md               letter -> the work-item directories in that block.
    coverage.md               commits in the repo that no block claims.

Run it from the repo root. It reads git and writes nothing outside the audit
directory. See .claude/skills/audit-skill/SKILL.md for what to do with the
output.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Accepts "sha;sha — Name: problem" with or without spaces around the dash,
# and tolerates the ASCII "--" some editors substitute.
LINE = re.compile(r"^\s*([0-9a-f;\s]+?)\s*(?:—|--)\s*(.+?)\s*:\s*(.+?)\s*$", re.S)
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def short(sha: str) -> str:
    return git("rev-parse", "--short", sha).strip()


def subject(sha: str) -> str:
    return git("log", "-1", "--format=%s", sha).strip()


def touched_tasks(sha: str) -> set[str]:
    """Work-item directories this commit wrote into."""
    files = git("show", "--name-only", "--format=", sha).split()
    return {m.group(1) for f in files if (m := re.match(r"docs/tasks/([^/]+)/", f))}


def is_work_item(sha: str) -> bool:
    """A commit that closed a chain — it wrote a review.md."""
    files = git("show", "--name-only", "--format=", sha).split()
    return any(re.match(r"docs/tasks/[^/]+/review\.md$", f) for f in files)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = Path(argv[1])
    if not src.exists():
        print(f"error: {src} does not exist", file=sys.stderr)
        return 1
    out = src.parent
    try:
        git("rev-parse", "--git-dir")
    except subprocess.CalledProcessError:
        print("error: run this from inside the repository", file=sys.stderr)
        return 1

    blocks: list[tuple[str, str, str, list[str]]] = []  # letter, name, problem, shas
    problems: list[str] = []

    for n, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            problems.append(f"line {n}: could not parse — {raw[:70]}")
            continue
        shas = [s for s in re.split(r"[;\s]+", m.group(1)) if s]
        resolved = []
        for s in shas:
            try:
                resolved.append(short(s))
            except subprocess.CalledProcessError:
                problems.append(f"line {n}: unknown commit {s}")
        if len(blocks) >= len(LETTERS):
            problems.append(f"line {n}: more than {len(LETTERS)} blocks")
            break
        blocks.append((LETTERS[len(blocks)], m.group(2), m.group(3), resolved))

    # ── a commit may belong to exactly one block ────────────────────────────
    seen: dict[str, str] = {}
    for letter, _name, _, shas in blocks:
        for s in shas:
            if s in seen:
                problems.append(
                    f"{s} ({subject(s)[:48]}) is claimed by block {seen[s]} and block {letter} "
                    f"— decide which one owns it before auditing"
                )
            seen[s] = letter

    if problems:
        print("Cannot prepare the audit:\n")
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    # ── warn where a block mixes work-item families ────────────────────────
    warnings: list[str] = []
    for letter, name, _, shas in blocks:
        fams: dict[str, list[str]] = {}
        for s in shas:
            for d in touched_tasks(s):
                fam = re.sub(r"[-_]?\d+.*$", "", d) or d
                fams.setdefault(fam, []).append(s)
        if len(fams) > 1:
            detail = "; ".join(f"{f} ({len(v)})" for f, v in sorted(fams.items()))
            warnings.append(f"block {letter} “{name}” spans work-item families: {detail}")

    # ── write ──────────────────────────────────────────────────────────────
    (out / "assignments").mkdir(parents=True, exist_ok=True)
    for letter, _, _, shas in blocks:
        body = [
            f"# Audit assignment {letter}",
            "",
            f"{len(shas)} commit(s), in order. They are one unit of purpose.",
            "",
        ]
        body += [f"    {s}" for s in shas]
        body += [
            "",
            "Start with `git show --stat` on each, then read the production-code",
            "diffs. Judge the net effect of all of them together.",
            "",
        ]
        (out / "assignments" / f"{letter}.md").write_text("\n".join(body), encoding="utf-8")

    key = [
        "# Audit key — DO NOT SHOW TO A PASS-ONE AUDITOR",
        "",
        "Open this only at step 3 of audit-skill, after every pass-one report is in.",
        "",
    ]
    for letter, name, problem, shas in blocks:
        key += [
            f"## {letter} — {name}",
            "",
            f"Commits: {', '.join(shas)}",
            "",
            f"**C (what the plan said):** {problem}",
            "",
        ]
    (out / "key.md").write_text("\n".join(key), encoding="utf-8")

    p2 = [
        "# Pass two — work items per block",
        "",
        "Safe to hand to the pass-two agent. It reads intent.md and spec.md in each",
        "directory below and reports what the work items *said* they would do.",
        "",
    ]
    for letter, _, _, shas in blocks:
        dirs = sorted({d for s in shas for d in touched_tasks(s)})
        p2.append(f"## {letter}")
        p2 += [f"- `docs/tasks/{d}/`" for d in dirs] or ["- (no work-item directory — see coverage.md)"]
        p2.append("")
    (out / "pass-two.md").write_text("\n".join(p2), encoding="utf-8")

    all_shas = [short(s) for s in git("rev-list", "--reverse", "HEAD").split()]
    uncovered = [s for s in all_shas if s not in seen]
    cov = [
        "# Coverage — commits no block claims",
        "",
        f"{len(all_shas) - len(uncovered)} of {len(all_shas)} commits are assigned to a block.",
        "",
        "## Closed work items that no block claims",
        "",
        "These wrote a review.md, so they were real units of work with their own",
        "intent. If one belongs to a block, add it and re-run. If it is genuinely",
        "outside the build order, say so here and move on.",
        "",
    ]
    loose = []
    for s in uncovered:
        (cov if is_work_item(s) else loose).append(f"- `{s}` — {subject(s)[:90]}")
    if len(cov) == 8:
        cov.append("- (none)")
    cov += ["", "## Other commits", "", "Harness, CLAUDE.md, blueprints, fixes. Normally fine to leave unaudited.", ""]
    cov += loose or ["- (none)"]
    (out / "coverage.md").write_text("\n".join(cov) + "\n", encoding="utf-8")

    # ── summary ────────────────────────────────────────────────────────────
    print(f"Prepared {len(blocks)} blocks in {out}/\n")
    for letter, name, _, shas in blocks:
        print(f"  {letter}  {len(shas):>2} commit(s)  {name[:58]}")
    print(f"\n  assignments/  {len(blocks)} files — pass one sees only these")
    print("  key.md        the answers — you only")
    print("  pass-two.md   work items per block")
    print(f"  coverage.md   {len(uncovered)} commit(s) unclaimed")
    if warnings:
        print("\nWorth a look before you start:")
        for w in warnings:
            print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
