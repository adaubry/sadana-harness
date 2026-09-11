"""web-search's one node body.

`docs/tasks/WEB-SEARCH-01-the-first-plugin-that-reaches-the-outside-world
/spec.md`. Every decision here delegates to `sadana.web_search`, which has no
I/O in it, so the only thing this file contributes that a unit test cannot
reach is the request itself.

The key comes from `plugins.read_setting` — the plugin's own declared
`[[setting]]`, supplied by the person through `sadana plugin set`. It is never
a model-supplied argument, never in this repository, and never in the URL.
"""

from sadana import execution, plugins, web_search

PLUGIN = "web-search"


def search(value: dict) -> str:
    # No no-key branch here on purpose. `PLUGIN-CONFIG-01`'s preflight in
    # `run_graph` refuses the run before any node starts when a declared
    # setting is unset, so a check written again here is unreachable — and
    # unreachable code a reader trusts as the live behaviour, with wording
    # that differs from what the preflight actually prints, is worse than
    # none. `assert` rather than a branch: if this ever fires, the preflight
    # stopped working and that is the bug worth hearing about.
    api_key = plugins.read_setting(PLUGIN, "api_key")
    assert api_key is not None, "run_graph's missing-settings preflight should have refused this run"

    query = str(value.get("query", "")).strip()
    if not query:
        return "No search query was given."
    # Normalised once, here at the boundary: `schema/search.json` is a hint
    # the model reads, not a contract anything enforces at run time, so
    # `count` really can arrive as a string.
    count = value.get("count", 5)
    if type(count) is not int:  # noqa: E721 - `isinstance` admits bool, and True would clamp to 1
        count = 5

    outcome = execution.run_http(
        execution.HttpRequest(
            method="GET",
            url=web_search.build_url(query, count),
            # The key rides in a header, never the query string, so it cannot
            # reach a log, a redirect or a referrer.
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
    )
    if isinstance(outcome, execution.Failure):
        return f"The search could not be completed: {outcome.detail}"

    parsed = web_search.parse(outcome.body)
    if isinstance(parsed, str):
        return parsed
    return web_search.render(query, parsed)
