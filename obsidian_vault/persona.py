"""Builds Tally's system prompt: the shared core from the vault, then this game's context layer.

The core (bootstrap.md, soul.md) is the same identity Tally has everywhere else.
It is read at startup and never written. Only the context layer lives in this repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config

FALLBACK_CORE = """\
(No core files were found, so Tally's identity could not be loaded.
Say so plainly when Kristian talks to you, and tell him to set tally.core_dir
in config.toml to the folder holding bootstrap.md and soul.md.
Do not invent a personality to fill the gap.)
"""


@dataclass
class Persona:
    system_prompt: str
    loaded: list[str]
    missing: list[str]

    @property
    def ok(self) -> bool:
        return bool(self.loaded) and not self.missing


def load_persona(cfg: Config) -> Persona:
    loaded, missing, parts = [], [], []

    if cfg.core_dir and cfg.core_dir.is_dir():
        for name in cfg.core_files:
            path = cfg.core_dir / name
            if path.is_file():
                parts.append(f'<core_file name="{name}">\n{path.read_text(encoding="utf-8")}\n</core_file>')
                loaded.append(name)
            else:
                missing.append(name)
    else:
        missing = list(cfg.core_files)

    core = "\n\n".join(parts) if parts else FALLBACK_CORE
    context = cfg.context_file.read_text(encoding="utf-8") if cfg.context_file.is_file() else ""

    system_prompt = (
        "# Shared core\n\n"
        "These files are Tally's identity, shared by every use case. They come from "
        "Kristian's vault and are the source of truth for who you are.\n\n"
        f"{core}\n\n"
        "# Context layer: The Obsidian Vault (game)\n\n"
        "Rules for this use case, layered on top of the core. Where they differ from "
        "the core's session procedures, they win for this session only; the core's "
        "personality and rules of conduct always apply.\n\n"
        f"{context}"
    )
    return Persona(system_prompt=system_prompt, loaded=loaded, missing=missing)


def vault_shelves(cfg: Config) -> list[dict]:
    """Theme folders in the vault, as shelf labels. Names and note counts only, never content."""
    if not cfg.core_dir or not cfg.core_dir.is_dir():
        return []
    shelves = []
    for d in sorted(cfg.core_dir.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")):
            notes = sum(1 for _ in d.rglob("*.md"))
            shelves.append({"name": d.name, "notes": notes})
    return shelves
