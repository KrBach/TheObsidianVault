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
Say so plainly when OWNER talks to you, and tell OWNER to set the Private vault
folder (the one holding bootstrap.md and soul.md) in the game's menu: Esc, then Settings.
Do not invent a personality to fill the gap.)
"""


_SHARED_WRITE_RULES = """- Your built-in tools stay read-only (Read, Glob, Grep, and WebSearch/WebFetch for lookups). The only way to write is the vault tools: append_interaction_log, add_coaching_item, edit_soul, log_failure, propose_edit, propose_note and vault_size. Never try another way.
- When OWNER corrects you or you catch your own mistake, add the failure-log entry with log_failure in this session, as soul.md's Failure log loop asks.
- The interaction log follows bootstrap.md section 4: one line per entry, about how the interaction went, not the work; at most one entry per session and theme unless something changes how you should behave or OWNER signals they are stopping ("that's it for now", "wrapping up", leaving the vault). The tool adds the timestamp and the "Project: The Obsidian Vault · Theme:" prefix; give the theme and, when there is one, the theme note's path as theme_link.
- Something for the coaching thread goes through add_coaching_item; mention it at a natural pause.
- Note edits and new notes are only proposals in the theme folders. A new note needs frontmatter with its theme as a tag, a "Related:" line and a link from its theme index (propose that edit too), as bootstrap.md and the notes skill say. Prefer one small exact edit over rewriting a passage.
- bootstrap.md, archive/, templates/ and hidden folders such as .obsidian/ cannot be written from here; if one of them needs a change, tell OWNER in one line so it can be done in a normal session.
- After each write, say in one short line what you wrote or queued. If the tool reports that the warm files are over the size guide, mention it once.
- If a tool refuses, say why in one line and do not try to get around it.
"""

WRITE_RULES = {
    "off": (
        "This session is read-only. Do not try to edit any file, and do not append to the interaction log in "
        "soul.md. If something happens here that the core says belongs in the log, tell OWNER in one line so they "
        "can bring it to a normal session."
    ),
    "review": (
        "You may write to the vault through the vault tools. soul.md is yours: interaction-log lines, coaching "
        "items and edit_soul changes are written at once, with a backup. Note edits and new notes in the theme "
        "folders wait for OWNER's approval at the desk (the ledger panel in the game), so for those say "
        "\"queued for your review\", not \"written\".\n\n" + _SHARED_WRITE_RULES
    ),
    "review-soul": (
        "You may write to the vault through the vault tools, and every change waits for OWNER's approval at "
        "the desk (the ledger panel in the game). Nothing is written until OWNER approves it, so say \"queued for "
        "your review\", not \"written\".\n\n" + _SHARED_WRITE_RULES
    ),
    "direct": (
        "You may write to the vault through the vault tools. soul.md is yours: interaction-log lines, coaching "
        "items and edit_soul changes are written at once, with a backup. Note edits and new notes wait for "
        "OWNER's approval at the desk (the ledger panel in the game).\n\n" + _SHARED_WRITE_RULES
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


def load_core(cfg: Config) -> tuple[str, list[str], list[str]]:
    """Tally's shared core (bootstrap.md, soul.md) as one text, with what loaded and what is missing."""
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
    return ("\n\n".join(parts) if parts else cfg.fill(FALLBACK_CORE)), loaded, missing


def load_persona(cfg: Config) -> Persona:
    core, loaded, missing = load_core(cfg)
    context = cfg.context_file.read_text(encoding="utf-8") if cfg.context_file.is_file() else ""

    if cfg.work_dir and cfg.work_dir.is_dir():
        work = (
            f"The Work vault is at {cfg.work_dir} and you can read it (start with INDEX.md there, then the note "
            "that fits, as bootstrap.md says for work tasks). It is read-only from the game: nothing you write "
            "may contain work content, and a work matter never goes into the Private interaction log. If a work "
            "task needs a note written or the Work log updated, say so in one line so OWNER can do it in a work session."
        )
    else:
        work = "Work notes are not loaded here. If OWNER asks about work, say this room only holds the Private vault."

    mode = "review-soul" if cfg.writes_mode == "review" and not cfg.soul_direct else cfg.writes_mode
    system_prompt = (
        "# Shared core\n\n"
        "These files are Tally's identity, shared by every use case. They come from "
        "OWNER's vault and are the source of truth for who you are.\n\n"
        "\x00CORE\x00\n\n"
        "# Context layer: The Obsidian Vault (game)\n\n"
        "Rules for this use case, layered on top of the core. Where they differ from "
        "the core's session procedures, they win for this session only; the core's "
        "personality and rules of conduct always apply.\n\n"
        f"{context}\n\n"
        f"## The Work vault\n\n{work}\n\n"
        f"## Writing to the vault (writes.mode = {cfg.writes_mode})\n\n"
        f"{WRITE_RULES.get(mode, WRITE_RULES['off'])}\n"
    )
    # The configured names go into this layer; the core files are the vault's own words, left as they are.
    system_prompt = cfg.fill(system_prompt).replace("\x00CORE\x00", core)
    return Persona(system_prompt=system_prompt, loaded=loaded, missing=missing)


GUEST_CONTEXT = Path(__file__).resolve().parent.parent / "context" / "tally-guest.md"


def load_guest_persona(cfg: Config) -> Persona:
    """Tally's public face, for a guest: none of the vault's private core (soul.md, bootstrap.md), no notes."""
    text = GUEST_CONTEXT.read_text(encoding="utf-8") if GUEST_CONTEXT.is_file() else "You are Tally, the vault's archivist. A guest is visiting; keep OWNER's notes private."
    return Persona(system_prompt=cfg.fill(text), loaded=["tally-guest.md"], missing=[])


def guest_shelves(cfg: Config) -> list[dict]:
    """Shelf labels a guest sees: as many shelves as there are, none of their names or sizes."""
    return [{"name": "a closed shelf" if s["name"] not in ("archive", "templates") else s["name"], "notes": 0}
            for s in vault_shelves(cfg)]


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
