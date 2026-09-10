"""Tests for sadana.subcommands.marketplace."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from sadana import gateway_daemon
from sadana.subcommands.marketplace import (
    build_marketplace_parser,
    cmd_marketplace_approve,
    cmd_marketplace_list,
    cmd_marketplace_reject,
    cmd_marketplace_serve_webhook,
    cmd_marketplace_show,
    cmd_marketplace_submit,
)


@pytest.mark.unit
def test_cmd_marketplace_submit_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    exit_code = cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))
    assert exit_code == 0
    assert "pending" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_marketplace_submit_unknown_tag_fails_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    exit_code = cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="no-such-tag"))
    assert exit_code == 1
    assert capsys.readouterr().err.strip() != ""


@pytest.mark.unit
def test_cmd_marketplace_list_shows_only_approved_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))
    capsys.readouterr()  # discard submit()'s own output

    cmd_marketplace_list(argparse.Namespace(pending=False))
    assert capsys.readouterr().out.strip() == ""

    cmd_marketplace_approve(argparse.Namespace(plugin_name="greeter", tag="v1.0.0"))
    cmd_marketplace_list(argparse.Namespace(pending=False))
    assert "greeter" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_marketplace_list_pending_shows_the_reviewer_queue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))
    cmd_marketplace_list(argparse.Namespace(pending=True))
    assert "greeter v1.0.0" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_marketplace_show_hides_a_pending_release_from_an_ordinary_browse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))

    exit_code = cmd_marketplace_show(argparse.Namespace(plugin_name="greeter", pending=False, as_json=False))
    assert exit_code == 1

    exit_code = cmd_marketplace_show(argparse.Namespace(plugin_name="greeter", pending=True, as_json=False))
    assert exit_code == 0
    assert "greeter" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_marketplace_show_json_prints_the_raw_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))
    cmd_marketplace_approve(argparse.Namespace(plugin_name="greeter", tag="v1.0.0"))

    cmd_marketplace_show(argparse.Namespace(plugin_name="greeter", pending=False, as_json=True))

    out = capsys.readouterr().out
    assert '"name": "greeter"' in out


@pytest.mark.unit
def test_cmd_marketplace_reject_without_reason_argument_type_error_is_not_possible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`reason` is a required positional in the parser; this proves the
    handler itself still refuses an empty one (e.g. `""`)."""
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))

    exit_code = cmd_marketplace_reject(argparse.Namespace(plugin_name="greeter", tag="v1.0.0", reason=""))

    assert exit_code == 1
    assert "reason" in capsys.readouterr().err


@pytest.mark.unit
def test_cmd_marketplace_reject_with_a_reason_succeeds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    cmd_marketplace_submit(argparse.Namespace(repo_url=str(repo), tag="v1.0.0"))

    exit_code = cmd_marketplace_reject(argparse.Namespace(plugin_name="greeter", tag="v1.0.0", reason="looks unsafe"))

    assert exit_code == 0
    assert "rejected" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_marketplace_approve_unknown_release_fails_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cmd_marketplace_approve(argparse.Namespace(plugin_name="nobody-submitted-this", tag="v1.0.0"))
    assert exit_code == 1
    assert "no release" in capsys.readouterr().err


@pytest.mark.unit
def test_cmd_marketplace_serve_webhook_refuses_to_start_when_secret_is_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SADANA_MARKETPLACE_WEBHOOK_SECRET", raising=False)

    def _fail_if_reached(**_kwargs: object) -> int:
        raise AssertionError("gateway_daemon.run should not have been reached")

    monkeypatch.setattr(gateway_daemon, "run", _fail_if_reached)

    result = cmd_marketplace_serve_webhook(argparse.Namespace(host=None, port=None))

    assert result == 1
    assert "SADANA_MARKETPLACE_WEBHOOK_SECRET" in capsys.readouterr().err


@pytest.mark.unit
def test_build_marketplace_parser_wires_every_subcommand() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_marketplace_parser(subparsers)

    submit_args = parser.parse_args(["marketplace", "submit", "https://example.invalid/greeter.git", "v1.0.0"])
    assert submit_args.func is cmd_marketplace_submit
    assert submit_args.repo_url == "https://example.invalid/greeter.git"
    assert submit_args.tag == "v1.0.0"

    list_args = parser.parse_args(["marketplace", "list", "--pending"])
    assert list_args.func is cmd_marketplace_list
    assert list_args.pending is True

    show_args = parser.parse_args(["marketplace", "show", "greeter", "--json"])
    assert show_args.func is cmd_marketplace_show
    assert show_args.plugin_name == "greeter"
    assert show_args.as_json is True

    approve_args = parser.parse_args(["marketplace", "approve", "greeter", "v1.0.0"])
    assert approve_args.func is cmd_marketplace_approve

    reject_args = parser.parse_args(["marketplace", "reject", "greeter", "v1.0.0", "looks unsafe"])
    assert reject_args.func is cmd_marketplace_reject
    assert reject_args.reason == "looks unsafe"

    webhook_args = parser.parse_args(["marketplace", "serve-webhook", "--port", "9000"])
    assert webhook_args.func is cmd_marketplace_serve_webhook
    assert webhook_args.port == 9000

    with pytest.raises(SystemExit):
        parser.parse_args(["marketplace"])
