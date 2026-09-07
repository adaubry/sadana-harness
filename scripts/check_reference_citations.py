#!/usr/bin/env python3
"""Fail a commit if tracked content cites a docs/reference/ path that isn't
tracked and isn't the one declared generated exception.

    scripts/check_reference_citations.py

Run by pre-commit (see .pre-commit-config.yaml), and by `make lint`/`make
verify` through it. See docs/tasks/A2-reference-tracking-scope/spec.md for
why: docs/reference/ mixes one regenerated index with hand-authored
analysis, and a citation into it must resolve for someone who clones this
repository fresh.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

CITATION_RE = re.compile(r"docs/reference/[\w.-]+\.(?:md|csv)")
ALLOWED_UNTRACKED = {"docs/reference/hermes_core_blocks_kind.csv"}


class Citation(NamedTuple):
    file: str
    line: int
    path: str


def find_broken_citations(citations: Iterable[Citation], tracked: set[str]) -> list[Citation]:
    """Pure: no git, no filesystem. Testable with fake input."""
    return [c for c in citations if c.path not in tracked and c.path not in ALLOWED_UNTRACKED]


def git_ls_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def extract_citations(tracked: Iterable[str]) -> list[Citation]:
    """I/O: reads every tracked file's text, except docs/reference/ itself —
    the corpus being cited should not also be scanned for self-citations."""
    citations = []
    for path in tracked:
        if path.startswith("docs/reference/"):
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in CITATION_RE.finditer(line):
                citations.append(Citation(path, lineno, m.group(0)))
    return citations


def main() -> int:
    tracked = set(git_ls_files())
    citations = extract_citations(tracked)
    broken = find_broken_citations(citations, tracked)
    for c in broken:
        print(f"{c.file}:{c.line}: cites {c.path}, which is not tracked and not the declared generated exception")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
