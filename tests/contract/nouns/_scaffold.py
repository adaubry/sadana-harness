"""Shared scaffolding for this artifact's own contract tests.

Named `_scaffold.py`, not `conftest.py`: pytest resolves a bare `from
conftest import ...` to whichever `conftest.py` sits nearest on `sys.path`,
and `tests/conftest.py` (the repo-wide one, already tracked, with its own
`make_runtime`/`EMPTY_PLUGIN_SET`) would lose that race to a same-named file
in this directory — a real, silent collision, not a style preference. A
handful of small helpers are re-declared below rather than risk it (the
amendment's own guidance: "add your own in your test file rather than
promoting theirs"). `schema`/`keys` still work as ordinary pytest fixtures
once a test file does `from _scaffold import schema, keys` — pytest
discovers a fixture by inspecting the importing module's own namespace, not
by where the function was first defined.

Deliberately not `tests/contract/test_console_grammar.py`'s own `door`
fixture either — that fixture is scoped to `harness`+`widgets` and isn't
built to be extended from outside its file (spec.md § Design, "Reusing
H19's test scaffolding without editing it"). Nothing here edits either file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from sadana import client_surface, ids, model_access, observability, plugin_dispatch, stores
from sadana.conversation_store import write_txn
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.router import DoorContext

HARNESS_ID = "hrn_test"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "console" / "grammar.json"


@pytest.fixture(scope="session")
def schema() -> dict:
    """Every test that validates a response depends on this — a missing
    schema file must fail loudly, never quietly skip the conformance proof
    it exists to give (matching `test_console_grammar.py`'s own posture)."""
    if not _SCHEMA_PATH.exists():
        pytest.fail("docs/console/grammar.json is missing")
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(schema: dict, instance: object, def_name: str) -> None:
    ref_schema = {"$ref": f"#/$defs/{def_name}", **{k: v for k, v in schema.items() if k != "$comment"}}
    jsonschema.Draft202012Validator(ref_schema).validate(instance)


@pytest.fixture
def keys(tmp_path: Path) -> SimpleNamespace:
    private_pem, jwk = auth.generate_dev_keypair("test")
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    return SimpleNamespace(private_pem=private_pem, jwks_path=jwks_path, kid="test")


def mint(keys: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(sub="u1", org=None, ws="ws1", hrn=HARNESS_ID, scope=[])
    defaults.update(overrides)
    return auth.mint_token(keys.private_pem, keys.kid, **defaults)  # type: ignore[arg-type]


def req(
    method: str, path: str, *, token: str | None, body: bytes = b"", headers: dict | None = None, query: str = ""
) -> door_request.DoorRequest:
    hdrs: dict[str, str] = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.setdefault("X-Sadana-Harness", HARNESS_ID)
    return door_request.DoorRequest(method=method, path=path, query=query, headers=hdrs, body=body)


def make_ctx(
    keys: SimpleNamespace, conns: stores.Connections, nouns: dict, *, extra_capabilities: tuple[str, ...] = ()
) -> DoorContext:
    """`extra_capabilities` lets a noun's own test (plan.md step 3) prove its
    capability-gated action works before `capabilities.py`'s own `DECLARED`
    is wired in (plan.md step 8) — merged in, never replacing, so a test
    that wants the real, currently-declared set still gets it."""
    verifier = auth.Verifier(auth.JwksSource(path=keys.jwks_path))
    return DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=tuple(dict.fromkeys((*capabilities_module.declared(), *extra_capabilities))),
        nouns=nouns,
        clock=time.time,
    )


EMPTY_PLUGIN_SET = plugin_dispatch.PluginSet(catalog=(), tool_specs=(), by_tool={})


def make_runtime(connections: stores.Connections) -> client_surface.Runtime:
    """`tests/conftest.py`'s own `make_runtime`, re-declared here to avoid
    the `conftest.py` name collision this file's own docstring explains."""
    return client_surface.Runtime(
        connections=connections,
        plugin_set=EMPTY_PLUGIN_SET,
        provider="p",
        model="m",
        recorder=observability.make_recorder(connections.writer),
    )


def seed_conversation(runtime: client_surface.Runtime, *, account: str, id: str) -> None:
    """A console-created conversation: `key == id`, owned by `account` — the
    one seeding shape every noun test under this directory needs (spec.md
    requirement 3; `client_surface.open_conversation`'s `id` keyword)."""
    client_surface.open_conversation(runtime, account=account, conversation=id, template_name="console", id=id)


def seed_turn_run(
    conn,
    *,
    conversation_key: str,
    turn_seq: int,
    exit_reason: str = "completed",
    model_calls: int = 1,
    duration_s: float = 1.5,
    recorded_at: float = 1000.0,
    state: str | None = None,
) -> str:
    """A hand-seeded `turn_runs` row — `runs.py`'s own tests don't need a
    real turn, just the row it would have produced (plan.md step 5). `state`
    defaults to what `exit_reason` implies (`done`/`failed`, the only two
    values production code ever writes today); a test proving the `stop`
    action's own state gate passes a real `running`/`waiting` value, which
    no production row carries yet but the grammar already declares."""
    run_id = ids.make_id("run")
    if state is None:
        state = "done" if exit_reason == "completed" else "failed"
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, prompt_tokens, "
            "completion_tokens, exit_reason, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_key,
                turn_seq,
                duration_s,
                model_calls,
                10,
                20,
                exit_reason,
                recorded_at,
                run_id,
                state,
                recorded_at - duration_s,
                recorded_at,
            ),
        )
    return run_id


def seed_plugin_run(
    conn,
    *,
    conversation_key: str,
    turn_seq: int,
    seq_in_turn: int = 0,
    plugin: str = "greeter",
    entry: str = "start",
    node_count: int = 1,
    failed_node: str | None = None,
    recorded_at: float = 1000.0,
) -> str:
    """A hand-seeded `plugin_runs` row (plan.md steps 3, 6)."""
    run_id = ids.make_id("run")
    state = "failed" if failed_node is not None else "closed"
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, duration_s, "
            "node_count, failed_node, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_key,
                turn_seq,
                seq_in_turn,
                plugin,
                entry,
                0.5,
                node_count,
                failed_node,
                recorded_at,
                run_id,
                state,
                recorded_at - 0.5,
                recorded_at,
            ),
        )
    return run_id


def seed_artifact(
    conn,
    *,
    conversation_key: str,
    turn_seq: int,
    seq_in_turn: int,
    name: str,
    kind: str = "file",
    ref: str = "",
    mime: str | None = "text/plain",
    size_bytes: int | None = None,
    at: float = 1000.0,
) -> str:
    """A hand-seeded `artifacts` row, mirroring `observability._insert_artifacts`."""
    artifact_id = ids.make_id("art")
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO artifacts (id, conversation_key, turn_seq, seq_in_turn, name, kind, ref, mime, "
            "size_bytes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, conversation_key, turn_seq, seq_in_turn, name, kind, ref, mime, size_bytes, at, at),
        )
    return artifact_id


def plain_response(content: str) -> model_access.Response:
    """`tests/conftest.py`'s own helper, re-declared here — same reason as
    `make_runtime`. A model response with no tool calls."""
    return model_access.Response(content=content, tool_calls=(), finish_reason="stop", usage=model_access.Usage())
