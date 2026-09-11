"""The `sadana persona` subcommand.

`docs/tasks/PERSONA-01-characters-you-write/spec.md`. Four verbs over a
directory of files the person owns: `list` to see what is there, `show` to
read one, `use` to choose one for an account, and `new` to write a template —
the last existing mostly so the directory is findable at all, which is half
the problem this work item is about.

No verb creates, edits, renames or deletes a character's *content*: those are
files, and `$EDITOR`, `mv` and `rm` already work on them. Commands here are a
second way to *reach* the one store, never a second store (spec.md § Rejected
alternatives).

Parser and handlers in one file, the shape `subcommands/memory.py` uses.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager

from sadana import persona, persona_store
from sadana.conversation_store import open_store, store_path_from_config
from sadana.persona import CharacterError


@contextmanager
def _open_schema_ensured_store() -> Iterator[sqlite3.Connection]:
    with closing(open_store(store_path_from_config())) as conn:
        persona_store.ensure_schema(conn)
        yield conn


def build_persona_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("persona", help="write characters and choose the one an account speaks in")
    persona_subparsers = parser.add_subparsers(dest="persona_command", required=True)

    list_parser = persona_subparsers.add_parser("list", help="show every character you have written")
    list_parser.add_argument("account", nargs="?", help="optional: mark which character this account is using")
    list_parser.set_defaults(func=cmd_persona_list)

    show_parser = persona_subparsers.add_parser("show", help="print one character's voice as the model receives it")
    show_parser.add_argument("name", help="the character's name, as shown by `persona list`")
    show_parser.set_defaults(func=cmd_persona_show)

    use_parser = persona_subparsers.add_parser("use", help="choose the character an account speaks in")
    use_parser.add_argument("account", help="the account choosing a character")
    use_parser.add_argument("name", help=f"the character's name, or {persona.NEUTRAL_NAME!r} for the neutral voice")
    use_parser.set_defaults(func=cmd_persona_use)

    new_parser = persona_subparsers.add_parser("new", help="write a template character file and print its path")
    new_parser.add_argument("name", help="the new character's name")
    new_parser.set_defaults(func=cmd_persona_new)


def cmd_persona_list(args: argparse.Namespace) -> int:
    characters_dir = persona_store.characters_dir_from_config()
    characters = persona_store.list_characters(characters_dir)
    selected = None
    if args.account is not None:
        with _open_schema_ensured_store() as conn:
            selected = persona_store.get_selection(conn, args.account)
    if not characters:
        print(f"No characters yet. They live in {characters_dir} — `sadana persona new <name>` writes one.")
    for character in characters:
        mark = "*" if character.name == selected else " "
        print(f"{mark} {character.name}: {character.description}")
    if selected is not None and selected not in {character.name for character in characters}:
        print(f"selected: {selected} (missing — using the neutral voice)")
    return 0


def cmd_persona_show(args: argparse.Namespace) -> int:
    try:
        character = persona_store.load_character(persona_store.characters_dir_from_config(), args.name)
    except CharacterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(persona.render(character))
    return 0


def cmd_persona_use(args: argparse.Namespace) -> int:
    if args.name == persona.NEUTRAL_NAME:
        with _open_schema_ensured_store() as conn:
            persona_store.clear_selection(conn, args.account)
        print(f"{args.account!r} now speaks in the neutral voice")
        return 0
    # Resolved before the store is opened: a name that does not load is a
    # typo, and a typo should not cost a write connection.
    try:
        persona_store.load_character(persona_store.characters_dir_from_config(), args.name)
    except CharacterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with _open_schema_ensured_store() as conn:
        persona_store.set_selection(conn, args.account, args.name, now=time.time())
    print(f"{args.account!r} now speaks as {args.name!r} — conversations already open keep the voice they started with")
    return 0


def cmd_persona_new(args: argparse.Namespace) -> int:
    try:
        path = persona_store.write_template(persona_store.characters_dir_from_config(), args.name)
    except (CharacterError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0
