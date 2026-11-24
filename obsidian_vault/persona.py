"""Builds Tally's system prompt: the shared core from the vault, then this game's context layer.

The core (bootstrap.md, soul.md) is the same identity Tally has everywhere else.
It is read at startup. Only the context layer lives in this repo. Writing to the vault goes
through the ledger's vault tools (see ledger.py); which rules Tally gets depends on writes.mode.
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


_SHARED_WRITE_RULES = """- Your built-in tools stay read-only (Read, Glob, Grep). The only way to write is the vault tools: append_interaction_log, add_coaching_item, edit_soul, propose_edit, propose_note and vault_size. Never try another way.
- The interaction log follows bootstrap.md section 4: one line per entry, about how the interaction went, not the work; at most one entry per session and theme unless something changes how you should behave or he signals he is stopping ("that's it for now", "wrapping up", leaving the vault). The tool adds the timestamp and the "Project: The Obsidian Vault · Theme:" prefix; give the theme and, when there is one, the theme note's path as theme_link.
- Something for the coaching thread goes through add_coaching_item; mention it at a natural pause.
- Note edits and new notes are only proposals in the theme folders. A new note needs frontmatter with its theme as a tag, a "Related:" line and a link from its theme index (propose that edit too), as bootstrap.md and the notes skill say. Prefer one small exact edit over rewriting a passage.
- bootstrap.md, archive/, templates/ and hidden folders such as .obsidian/ cannot be written from here; if one of them needs a change, tell him in one line so he can do it in a normal session.
- After each write, say in one short line what you wrote or queued. If the tool reports that the warm files are over the size guide, mention it once.
- If a tool refuses, say why in one line and do not try to get around it.
"""

WRITE_RULES = {
    "off": (
        "This session is read-only. Do not try to edit any file, and do not append to the interaction log in "
        "soul.md. If something happens here that the core says belongs in the log, tell him in one line so he "
        "can bring it to a normal session."
    ),
    "review": (
        "You may write to the vault through the vault tools. soul.md is yours: interaction-log lines, coaching "
        "items and edit_soul changes are written at once, with a backup. Note edits and new notes in the theme "
        "folders wait for Kristian's approval at the desk (the ledger panel in the game), so for those say "
        "\"queued for your review\", not \"written\".\n\n" + _SHARED_WRITE_RULES
    ),
    "review-soul": (
        "You may write to the vault through the vault tools, and every change waits for Kristian's approval at "
        "the desk (the ledger panel in the game). Nothing is written until he approves it, so say \"queued for "
        "your review\", not \"written\".\n\n" + _SHARED_WRITE_RULES
    ),
    "direct": (
        "You may write to the vault through the vault tools. soul.md is yours: interaction-log lines, coaching "
        "items and edit_soul changes are written at once, with a backup. Note edits and new notes wait for "
        "Kristian's approval at the desk (the ledger panel in the game).\n\n" + _SHARED_WRITE_RULES
    ),
}


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

    mode = "review-soul" if cfg.writes_mode == "review" and not cfg.soul_direct else cfg.writes_mode
    system_prompt = (
        "# Shared core\n\n"
        "These files are Tally's identity, shared by every use case. They come from "
        "Kristian's vault and are the source of truth for who you are.\n\n"
        f"{core}\n\n"
        "# Context layer: The Obsidian Vault (game)\n\n"
        "Rules for this use case, layered on top of the core. Where they differ from "
        "the core's session procedures, they win for this session only; the core's "
        "personality and rules of conduct always apply.\n\n"
        f"{context}\n\n"
        f"## Writing to the vault (writes.mode = {cfg.writes_mode})\n\n"
        f"{WRITE_RULES.get(mode, WRITE_RULES['off'])}\n"
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
