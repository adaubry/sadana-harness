"""Building a `DoorContext`: the noun registry, the capability list, and
`resume_on_start` — one function, so a new transport and the existing
loopback listener never drift apart on what they serve.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. `console_fit_plan.md`
§5(e): "One door, two transports" — the tether (H30) and the loopback
listener (H19) both call the same pure `router.handle()`; this is what
makes sure they hand it the same `DoorContext` shape rather than two
independently-maintained noun dictionaries. Calls `operations.resume_on_start`
itself, so a caller cannot forget it — the same "one place assembles this,
everyone else calls it" shape `client_surface.open_runtime()` already
established.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from sadana import client_surface
from sadana.door import capabilities
from sadana.door.auth import BoxIdentity, Verifier
from sadana.door.nouns import (
    NounModule,
    agent_templates,
    agents,
    approvals,
    artifacts,
    harness,
    memory_entries,
    memory_policies,
    runs,
    schedules,
    spans,
    traces,
)
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.nouns.messages import MessagesNoun
from sadana.door.operations import resume_on_start
from sadana.door.router import DoorContext


def build(
    turn_runtime: client_surface.Runtime,
    *,
    box_identity: BoxIdentity,
    verifier: Verifier,
    clock: Callable[[], float] = time.time,
) -> DoorContext:
    conns = turn_runtime.connections
    resume_on_start(conns)
    nouns: Mapping[str, NounModule] = {
        "artifacts": artifacts,
        "conversations": ConversationsNoun(turn_runtime),
        "harness": harness,
        "agents": agents,
        "agent_templates": agent_templates,
        "memory_entries": memory_entries,
        "memory_policies": memory_policies,
        "schedules": schedules,
        "messages": MessagesNoun(turn_runtime),
        "runs": runs,
        "spans": spans,
        "traces": traces,
        "approvals": approvals,
    }
    return DoorContext(
        conns=conns,
        runtime=box_identity,
        verifier=verifier,
        capabilities=capabilities.declared(),
        nouns=nouns,
        clock=clock,
    )
