"""The `sadana plugin` subcommand.

`docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/spec.md`. Owns
both its parser and its handlers in one file, mirroring
`subcommands/runs.py`'s shape. Every `RegisterOutcome`/`InstallOutcome`
variant maps to a stderr line and an exit code here — `plugin_install.py`
itself never prints or exits.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from contextlib import closing

from sadana import env_file, plugin_install, plugin_manifest, plugins
from sadana.conversation_store import open_store, store_path_from_config


def build_plugin_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plugin", help="register and install plugins from a git repository")
    plugin_subparsers = parser.add_subparsers(dest="plugin_command", required=True)

    register_parser = plugin_subparsers.add_parser("register", help="record a name for a plugin's git repository")
    register_parser.add_argument("name", help="the name to register")
    register_parser.add_argument("repo_url", help="the plugin's git repository URL")
    register_parser.set_defaults(func=cmd_plugin_register)

    install_parser = plugin_subparsers.add_parser("install", help="fetch, verify, and place a registered plugin")
    install_parser.add_argument("name", help="a name already registered")
    install_parser.add_argument("tag", help="the exact released git tag to install")
    install_parser.add_argument(
        "--replace", action="store_true", help="replace an already-installed plugin of this name"
    )
    install_parser.set_defaults(func=cmd_plugin_install)

    settings_parser = plugin_subparsers.add_parser(
        "settings", help="what an installed plugin needs from you, and what is still missing"
    )
    settings_parser.add_argument("name", help="an installed plugin")
    settings_parser.set_defaults(func=cmd_plugin_settings)

    set_parser = plugin_subparsers.add_parser("set", help="store one of a plugin's declared settings")
    set_parser.add_argument("name", help="an installed plugin")
    set_parser.add_argument("setting", help="a setting that plugin declares")
    set_parser.add_argument(
        "--value",
        metavar="VALUE",
        default=None,
        help="the value to store (prompted for, masked if it is a secret, when not given)",
    )
    set_parser.set_defaults(func=cmd_plugin_set)


_NAME_RULE = (
    "lowercase letters and digits joined by single dashes, "
    "not starting or ending with a dash, at most 64 letters or digits"
)


def _installed(name: str) -> plugins.InstalledPlugin | None:
    """The installed plugin of that name, or ``None``. Reuses
    ``discover_plugins`` rather than reading one directory directly, so an
    installed-but-invalid plugin is not-found here for exactly the same
    reason it is not-found to the agent.
    """
    for installed in plugin_manifest.discover_plugins():
        if installed.name == name:
            return installed
    return None


def _render_settings(manifest: plugins.Manifest) -> list[str]:
    """One line per declared setting: what it is called, whether it is a
    secret, whether it currently has a value, and what it is for. Never the
    value itself — that is the whole point of this being a separate
    rendering rather than a dump."""
    if not manifest.settings:
        return [f"{manifest.name} needs no settings."]
    lines = [f"{manifest.name} needs:"]
    for setting in manifest.settings:
        kind = "secret" if setting.secret else "setting"
        state = "set" if plugins.read_setting(manifest.name, setting.name) is not None else "NOT SET"
        lines.append(f"  {setting.name}  [{kind}, {state}]  {setting.purpose}")
    lines.append(f"Set one with: sadana plugin set {manifest.name} <setting>")
    return lines


def cmd_plugin_register(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = plugin_install.register(conn, args.name, args.repo_url, now=time.time())
    match outcome:
        case plugin_install.Registered():
            print(f"registered {args.name!r} -> {args.repo_url}")
            return 0
        case plugin_install.NameTaken():
            print(f"error: {args.name!r} is already registered", file=sys.stderr)
            return 1
        case plugin_install.InvalidName():
            print(f"error: {args.name!r} is not a valid plugin name: {_NAME_RULE}", file=sys.stderr)
            return 1


def cmd_plugin_install(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = plugin_install.install(
            conn, args.name, args.tag, plugins_root=plugins._plugins_root(), replace=args.replace
        )
    match outcome:
        case plugin_install.Installed(directory=directory):
            print(f"installed {args.name!r} at {args.tag!r} -> {directory}")
            # The moment a person learns what a plugin will need is the moment
            # they install it, not the first time a run fails
            # (`docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns
            # /intent.md`).
            # `validate()` on the directory just handed back, not a lookup by
            # name: the name never becomes a path again, and the whole plugin
            # set does not get re-walked to re-find what was just installed.
            #
            # `check_bodies=False` is the load-bearing half. `validate()`'s
            # default imports and runs the plugin's own `init.py`, and this
            # tree was cloned from a stranger seconds ago — CLAUDE.md: a
            # caller handling input from a source it doesn't already trust
            # uses the non-executing mode. `marketplace.submit()` passes the
            # same flag for the same reason. Only `.settings` is read here,
            # and that needs no body resolved.
            checked = plugin_manifest.validate(directory, check_bodies=False)
            if isinstance(checked, plugins.Valid) and checked.manifest.settings:
                for line in _render_settings(checked.manifest):
                    print(line)
            return 0
        case plugin_install.UnknownPluginName():
            print(f"error: {args.name!r} is not registered", file=sys.stderr)
            return 1
        case plugin_install.AlreadyInstalled():
            print(f"error: {args.name!r} is already installed; pass --replace to reinstall", file=sys.stderr)
            return 1
        case plugin_install.NameMismatch(expected=expected, found=found):
            print(f"error: plugin.toml declares {found!r}, expected {expected!r}", file=sys.stderr)
            return 1
        case plugin_install.InvalidName():
            print(f"error: {args.name!r} is not a valid plugin name: {_NAME_RULE}", file=sys.stderr)
            return 1
        case plugin_install.TagMismatch() | plugin_install.FetchFailed():
            print(f"error: {plugin_install.describe_fetch_failure(outcome)}", file=sys.stderr)
            return 1


def cmd_plugin_settings(args: argparse.Namespace) -> int:
    installed = _installed(args.name)
    if installed is None:
        print(f"error: {args.name!r} is not installed", file=sys.stderr)
        return 1
    for line in _render_settings(installed.manifest):
        print(line)
    return 0


def cmd_plugin_set(args: argparse.Namespace) -> int:
    installed = _installed(args.name)
    if installed is None:
        print(f"error: {args.name!r} is not installed", file=sys.stderr)
        return 1
    declared = {s.name: s for s in installed.manifest.settings}
    setting = declared.get(args.setting)
    if setting is None:
        known = ", ".join(sorted(declared)) or "nothing"
        print(f"error: {args.name!r} declares no setting {args.setting!r}; it declares {known}", file=sys.stderr)
        return 1

    value = args.value
    if value is None:
        prompt = f"{setting.purpose}: "
        try:
            if not sys.stdin.isatty():
                # Read it from stdin rather than demanding `--value`. Anything
                # in argv is world-readable under /proc and lands in shell
                # history, so a scripted `plugin set` should never be forced
                # to put a credential there: `... | sadana plugin set x k`.
                value = sys.stdin.readline().rstrip("\n")
            elif setting.secret:
                value = getpass.getpass(prompt)
            else:
                value = input(prompt)
        except (KeyboardInterrupt, EOFError):
            print("cancelled", file=sys.stderr)
            return 1
    if not value:
        print(
            "error: refusing to store an empty value; an empty setting reads as missing"
            + ("" if sys.stdin.isatty() else " (pipe the value on stdin, or pass --value)"),
            file=sys.stderr,
        )
        return 1

    # Never inside the plugin's own directory: an update would overwrite it
    # and a backup of that directory would carry it somewhere it should not go.
    path = env_file.env_path()
    env_file.upsert_key(path, plugins.setting_env_var(args.name, args.setting), value)
    print(f"stored {args.setting!r} for {args.name!r} in {path}")
    return 0
