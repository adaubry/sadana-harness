"""How sadana loads the user's persona — the system prompt's own stable part.

Moved out of `subcommands/chat.py` (CLI-SHELL-04) once a second real caller
needed the exact same behavior: `sadana gateway run`
(`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`) reads the
user's configured persona for every reply it sends, the same way `sadana
chat` always has. Two real callers is what earns a shared module — see
CLAUDE.md's registry-of-one rule, applied here to file layout rather than to
a dispatch seam.
"""

from __future__ import annotations

from pathlib import Path

from sadana import config

_DEFAULT_PERSONA = (
    "You are sadana, a plainly-spoken assistant. Answer directly, say "
    "when you're not sure, and only act on something after it has "
    "actually been agreed to.\n"
)


def persona_path_from_config() -> Path:
    return config.env_path("SADANA_CHAT_PERSONA_PATH", default=config.get_paths().config_dir / "persona.md")


def load_or_seed_persona(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_PERSONA, encoding="utf-8")
    return path.read_text(encoding="utf-8")
