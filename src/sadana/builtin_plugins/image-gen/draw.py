"""image-gen's one node body.

`docs/tasks/IMAGE-GEN-01-the-first-plugin-that-makes-a-file/spec.md`. Every
decision delegates to `sadana.image_gen`; what is here is the HTTP call, the
file write, and the `Artifact` that names it.

**The picture's bytes must not leave this function except into the file.** A
node returns one value, and this one returns an `Artifact`, so `run_graph`
threads `artifact.ref` — the path — onward and there is no channel by which
the base64 could accompany it. A body that returned the data URI instead would
work perfectly and silently consume most of the room the conversation had
left, which is why the guarantee is structural rather than a rule to
remember.
"""

from pathlib import Path

from sadana import artifact_store, execution, image_gen, plugins
from sadana.plugins import Artifact

PLUGIN = "image-gen"


def draw(value: dict) -> Artifact | str:
    api_key = plugins.required_setting(PLUGIN, "api_key")

    prompt = str(value.get("prompt", "")).strip()
    if not prompt:
        return "No description was given of what to draw."

    url, body = image_gen.build_request(prompt)
    outcome = execution.run_http(
        execution.HttpRequest(
            method="POST",
            url=url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            body=body,
            timeout_s=image_gen.timeout_s(),
        )
    )
    if isinstance(outcome, execution.Failure):
        return f"The picture could not be drawn: {outcome.detail}"

    decoded = image_gen.picture(outcome.body)
    if isinstance(decoded, str):
        return decoded
    pixels, extension = decoded

    # `output_dir()` creates the run's own directory on this first ask and
    # nowhere else; `run_graph` re-checks the returned ref is inside it, so
    # no containment check is written again here.
    name = image_gen.filename(prompt, extension)
    path: Path = artifact_store.output_dir() / name
    try:
        path.write_bytes(pixels)
    except OSError as exc:
        return f"The picture was drawn but could not be saved: {exc.strerror or 'write failed'}"

    return Artifact(kind="file", name=name, ref=str(path))
