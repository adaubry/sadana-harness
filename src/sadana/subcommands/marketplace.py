"""The `sadana marketplace` subcommand.

`docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/spec.md`. Owns
both its parser and its handlers, mirroring `subcommands/plugin.py`'s
shape. There is no separate "become a reviewer" step: `--pending` is how
anyone chooses, for that one command, to see what an ordinary browse never
shows (Requirement 3's "role is whichever action someone takes").
"""

from __future__ import annotations

import argparse
import http.server
import json
import sys
import time
from contextlib import closing
from typing import cast

from sadana import config, gateway_daemon, marketplace, marketplace_webhook, plugin_install
from sadana.conversation_store import open_store, store_path_from_config
from sadana.marketplace import ReleaseListing


def build_marketplace_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("marketplace", help="submit, review, and browse plugins")
    market_subparsers = parser.add_subparsers(dest="marketplace_command", required=True)

    submit_parser = market_subparsers.add_parser("submit", help="submit a plugin release for review")
    submit_parser.add_argument("repo_url", help="the plugin's git repository URL")
    submit_parser.add_argument("tag", help="the released git tag to submit")
    submit_parser.set_defaults(func=cmd_marketplace_submit)

    list_parser = market_subparsers.add_parser("list", help="list plugins")
    list_parser.add_argument("--pending", action="store_true", help="list releases awaiting review instead")
    list_parser.set_defaults(func=cmd_marketplace_list)

    show_parser = market_subparsers.add_parser("show", help="show one plugin's declared shape")
    show_parser.add_argument("plugin_name")
    show_parser.add_argument("--pending", action="store_true", help="show its pending release instead of approved")
    show_parser.add_argument("--json", action="store_true", dest="as_json", help="print the raw manifest as JSON")
    show_parser.set_defaults(func=cmd_marketplace_show)

    approve_parser = market_subparsers.add_parser("approve", help="approve a pending release")
    approve_parser.add_argument("plugin_name")
    approve_parser.add_argument("tag")
    approve_parser.set_defaults(func=cmd_marketplace_approve)

    reject_parser = market_subparsers.add_parser("reject", help="reject a pending release")
    reject_parser.add_argument("plugin_name")
    reject_parser.add_argument("tag")
    reject_parser.add_argument("reason")
    reject_parser.set_defaults(func=cmd_marketplace_reject)

    webhook_parser = market_subparsers.add_parser(
        "serve-webhook", help="listen for tag-push webhooks and submit releases automatically"
    )
    webhook_parser.add_argument("--host", help="override the configured bind host")
    webhook_parser.add_argument("--port", type=int, help="override the configured bind port")
    webhook_parser.set_defaults(func=cmd_marketplace_serve_webhook)


def cmd_marketplace_submit(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = marketplace.submit(conn, args.repo_url, args.tag, now=time.time())
    match outcome:
        case marketplace.Pending(plugin_name=plugin_name, tag=tag):
            print(f"{plugin_name!r} {tag!r} is pending review")
            return 0
        case marketplace.NameOwnedByAnotherRepo(plugin_name=plugin_name, owning_repo_url=owning_repo_url):
            print(f"error: {plugin_name!r} is already claimed by {owning_repo_url!r}", file=sys.stderr)
            return 1
        case marketplace.AlreadySubmitted(plugin_name=plugin_name, tag=tag):
            print(f"error: {plugin_name!r} {tag!r} was already submitted", file=sys.stderr)
            return 1
        case marketplace.InvalidManifest(detail=detail):
            print(f"error: {detail}", file=sys.stderr)
            return 1
        case plugin_install.TagMismatch() | plugin_install.FetchFailed():
            print(f"error: {plugin_install.describe_fetch_failure(outcome)}", file=sys.stderr)
            return 1


def _print_listing(listing: ReleaseListing, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(listing.manifest, indent=2))
        return
    manifest = listing.manifest
    print(f"{manifest['name']} {manifest['version']} ({listing.status})")
    print(manifest["description"])
    for entry in cast(list, manifest["entries"]):
        print(f"  entry {entry['tool']}: {entry['purpose']}")
    for node in cast(list, manifest["nodes"]):
        target = node["next"] or (", ".join(node["ports"]) if node["ports"] else "(terminal)")
        print(f"  node {node['name']} [{node['kind']}] -> {target}")
    if listing.reason:
        print(f"reason: {listing.reason}")


def cmd_marketplace_list(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        if args.pending:
            for release in marketplace.pending_releases(conn):
                print(f"{release.plugin_name} {release.tag} (pending)")
        else:
            for name in marketplace.approved_plugin_names(conn):
                print(name)
    return 0


def cmd_marketplace_show(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        listing = (
            marketplace.latest_pending(conn, args.plugin_name)
            if args.pending
            else marketplace.latest_approved(conn, args.plugin_name)
        )
    if listing is None:
        print(
            f"error: no {'pending' if args.pending else 'approved'} release for {args.plugin_name!r}", file=sys.stderr
        )
        return 1
    _print_listing(listing, as_json=args.as_json)
    return 0


def cmd_marketplace_approve(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = marketplace.decide(conn, args.plugin_name, args.tag, "approved", now=time.time())
    return _report_decide_outcome(outcome)


def cmd_marketplace_reject(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = marketplace.decide(conn, args.plugin_name, args.tag, "rejected", reason=args.reason, now=time.time())
    return _report_decide_outcome(outcome)


def _report_decide_outcome(outcome: marketplace.DecideOutcome) -> int:
    match outcome:
        case marketplace.Decided(plugin_name=plugin_name, tag=tag, status=status):
            print(f"{plugin_name!r} {tag!r} is now {status}")
            return 0
        case marketplace.UnknownRelease(plugin_name=plugin_name, tag=tag):
            print(f"error: no release {plugin_name!r} {tag!r}", file=sys.stderr)
            return 1
        case marketplace.ReasonRequired():
            print("error: a rejection needs a reason", file=sys.stderr)
            return 1
        case marketplace.AlreadyDecided(plugin_name=plugin_name, tag=tag, status=status):
            print(f"error: {plugin_name!r} {tag!r} is already {status}", file=sys.stderr)
            return 1


def cmd_marketplace_serve_webhook(args: argparse.Namespace) -> int:
    host = args.host or config.env("SADANA_MARKETPLACE_HOST", "127.0.0.1")
    port = args.port or config.env_int("SADANA_MARKETPLACE_PORT", 8766)
    secret = config.env("SADANA_MARKETPLACE_WEBHOOK_SECRET", "")
    if not secret:
        print("SADANA_MARKETPLACE_WEBHOOK_SECRET is not set; refusing to start", file=sys.stderr)
        return 1

    def on_release(submission: marketplace_webhook.ReleaseSubmission) -> tuple[bool, str]:
        with closing(open_store(store_path_from_config())) as conn:
            outcome = marketplace.submit(conn, submission.repo_url, submission.tag, now=time.time())
        return isinstance(outcome, marketplace.Pending), repr(outcome)

    def make_server() -> http.server.ThreadingHTTPServer:
        return marketplace_webhook.make_server(host, port, secret=secret, on_release=on_release)

    return gateway_daemon.run(make_server=make_server, lock_filename="marketplace.lock")
