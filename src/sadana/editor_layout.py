"""Where each box sits on the canvas, worked out from the graph itself.

`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md`. Pure — no disk, no
clock, no network — which is the point: this is presentation logic that would
normally live in the browser, kept in Python because nothing in this
repository can test JavaScript (CLAUDE.md: "A browser surface holds no logic
that can be held in Python").

Positions are computed on every read and stored nowhere. The requester chose
that at design time over keeping coordinates in the plugin's own description
or in a file beside it, and the argument that decided it is worth keeping
here: a plugin someone wrote by hand, or installed from the marketplace, can
never carry coordinates, so a computed layout has to exist in every possible
design. Storing them would mean building this *and* a storage mechanism.

Determinism is what makes reopening a plugin show the same picture without
storing anything, so it is a property this module owes its caller, not an
accident of the walk.
"""

from __future__ import annotations

from collections import deque

from sadana import plugins

# Enough room for a box plus its arrow. Not configurable: a person adjusting
# canvas spacing is a preference with no caller, and `config` is for
# behaviours something actually reads (CLAUDE.md).
_COLUMN_WIDTH = 220
_ROW_HEIGHT = 110
_MARGIN = 40


def _depths(manifest: plugins.Manifest) -> dict[str, int]:
    """How far each node sits from the entry that reaches it, by breadth-first
    walk from every entry's own ``start`` in declared order. First discovery
    wins, so a node two paths reach lands at the shallower of the two.

    Tolerates the two shapes a *finished* plugin never has but a plugin being
    drawn has constantly: a cycle (the visited set ends the walk) and a node
    no entry reaches (absent from the result, placed by the caller)."""
    by_name = plugins._node_index(manifest)
    depth: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque((entry.start, 0) for entry in manifest.entries)
    while queue:
        name, at = queue.popleft()
        if name in depth or name not in by_name:
            continue
        depth[name] = at
        queue.extend((successor, at + 1) for successor in plugins._successors(by_name[name]))
    return depth


def positions(manifest: plugins.Manifest) -> dict[str, tuple[int, int]]:
    """A pixel position for every declared node, including ones no entry
    reaches — the editor has to draw those precisely so the person can see
    what validation is complaining about.

    Column is distance from the entry; row is the order the node was declared
    within its column. Unreachable nodes go in one column past the deepest
    reached one, so they read as visibly set apart rather than tangled into
    the flow."""
    depth = _depths(manifest)
    unreachable_column = max(depth.values(), default=-1) + 1
    rows: dict[int, int] = {}
    result: dict[str, tuple[int, int]] = {}
    for node in manifest.nodes:
        column = depth.get(node.name, unreachable_column)
        row = rows.get(column, 0)
        rows[column] = row + 1
        result[node.name] = (_MARGIN + column * _COLUMN_WIDTH, _MARGIN + row * _ROW_HEIGHT)
    return result
