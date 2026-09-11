"""Everything about driving a browser that does not run one.

`docs/tasks/BROWSE-01-driving-a-browser-someone-else-wrote/spec.md`. The
plugin's body beside it runs one program and delegates every decision here.

**The script below is first-party and fixed.** The model contributes exactly
one thing — the task string — and it arrives as its own element of `argv`,
never concatenated into anything. That is the entire difference between this
and what the reference does: hermes's `browser_exec` takes `code` from the
model and pipes it to a browser (`tools/browser_use_cli.py:1057-1061`), which
is Shape C by the capability blueprint's own §4.3, and a plugin may not be
driven. The blueprint's §4.2 claimed otherwise and has been corrected.

browser-use is run as a program through `uvx`, not imported, so none of its
hundred-odd packages enter this project's dependencies.
"""

from __future__ import annotations

from sadana import config
from sadana.execution import Ran
from sadana.untrusted_text import defang_block, fenced

# Pinned, so the evidence in review.md is reproducible and so an upgrade
# cannot silently move the API `AGENT_SCRIPT` is written against — that script
# is a string nothing type-checks, and its only real check is running it.
VERSION = "0.13.10"

DEFAULT_MODEL = "openai/gpt-4.1-mini"

# What a browsing run may take. Browsing is slower again than drawing: each
# step is a page load plus a model call, and there are many steps.
DEFAULT_TIMEOUT_S = 300

MAX_REPORT_CHARS = 8_000

# The script, held as a constant and never built from anything. It reads the
# task from `sys.argv[1]` — a separate argument, so a task containing quotes,
# semicolons or newlines is data and cannot become code.
#
# Kept short on purpose: it is a string, so nothing here type-checks or lints
# it, and its first real execution is `scripts/prove_browse.py`.
AGENT_SCRIPT = """
import asyncio, os, sys
from browser_use import Agent
from browser_use.llm.openrouter.chat import ChatOpenRouter

async def main():
    llm = ChatOpenRouter(model=os.environ["BROWSE_MODEL"], api_key=os.environ.pop("OPENROUTER_API_KEY"))
    result = await Agent(task=sys.argv[1], llm=llm).run()
    print(result.final_result() or "(the browser finished without reporting anything)")

asyncio.run(main())
"""


def model() -> str:
    return config.env("SADANA_BROWSE_MODEL", DEFAULT_MODEL)


def timeout_s() -> int:
    return config.env_int("SADANA_BROWSE_TIMEOUT_S", DEFAULT_TIMEOUT_S)


def build_env(api_key: str) -> dict[str, str]:
    """What the child is handed. `run_program` builds the rest of its
    environment from an allowlist, so nothing else is inherited.

    Here rather than in the body so both ends of the `BROWSE_MODEL` contract —
    written here, read inside `AGENT_SCRIPT` above — sit within a few lines of
    each other. Split across two files there is no checker, and a rename in
    one is silent.

    `ANONYMIZED_TELEMETRY` is the third entry and it is not an optimisation.
    browser-use defaults it to *true*, and the event it then sends on every
    run carries `task`, `urls_visited`, `action_history` and
    `final_result_response` — the goal, every page visited, and the answer, to
    a third party. This item's own spec enumerates two exposures (the model,
    and the pages reaching it); that would have been a silent third. hermes
    disables it for the same reason
    (`tools/browser_use_cli.py:126`) and copying that decision is exactly what
    CLAUDE.md's methodology asks for."""
    return {
        "OPENROUTER_API_KEY": api_key,
        "BROWSE_MODEL": model(),
        "ANONYMIZED_TELEMETRY": "false",
    }


def build_argv(task: str) -> tuple[str, ...]:
    """The full invocation. `-c` terminates option parsing, so everything
    after the script is a positional — a task starting with `-` cannot be read
    as an option to `python`, which is what CLAUDE.md's `--` rule is about."""
    return ("uvx", "--from", f"browser-use=={VERSION}", "python", "-c", AGENT_SCRIPT, task)


def summarise(ran: Ran) -> str:
    """What the model reads.

    Defanged *and fenced*, because this is a page's own contents arriving in
    front of a model — the same untrusted-text path a search result takes, and
    the first one where the text was chosen by software rather than by a
    person. Returning it unlabelled would read as this plugin's own report.

    `defang_block` rather than `defang`: a page report is a table or several
    paragraphs, and collapsing its whitespace the way a 500-character snippet
    is collapsed would arrive as one line."""
    if ran.exit_code != 0:
        # The *tail*, not the head. browser-use logs every step to stderr at
        # info level, so a five-minute run puts kilobytes of chatter in front
        # of whatever actually went wrong — and keeping the beginning means
        # reporting the startup banner and cutting the cause. hermes takes the
        # last few lines for the same reason (`browser_use_cli.py:410`).
        detail = defang_block(ran.stderr or ran.stdout or "no output")[-MAX_REPORT_CHARS:]
        return f"The browser could not finish the task: {detail}"
    report = defang_block(ran.stdout)[:MAX_REPORT_CHARS]
    if not report:
        return "The browser finished without reporting anything."
    return fenced("What a browser read on the page", report)
