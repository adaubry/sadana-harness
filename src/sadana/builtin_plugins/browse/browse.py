"""browse's one node body.

`docs/tasks/BROWSE-01-driving-a-browser-someone-else-wrote/spec.md`. Runs one
program and delegates every decision to `sadana.browse`.

The model supplies the task and nothing else. It does not supply code, does not
issue steps, and is not consulted again until the run is over — which is what
makes this Shape B rather than the Shape C the reference actually ships
(capability blueprint §4.2, as corrected).

What this does not claim: browser-use decides its own actions, reads pages
nobody vetted, and runs with the person's own access. `execution.run_program`
says plainly that it is not a boundary, and `intent.md` records the owner
accepting that.
"""

from sadana import browse as pure
from sadana import execution, plugins

PLUGIN = "browse"


def browse(value: dict) -> str:
    api_key = plugins.required_setting(PLUGIN, "api_key")

    task = str(value.get("task", "")).strip()
    if not task:
        return "No task was given to carry out in the browser."

    outcome = execution.run_program(
        execution.ProgramRequest(
            argv=pure.build_argv(task),
            env=pure.build_env(api_key),
            timeout_s=pure.timeout_s(),
        )
    )
    if isinstance(outcome, execution.Failure):
        return f"The browser could not be run: {outcome.detail}"
    return pure.summarise(outcome)
