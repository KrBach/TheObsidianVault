"""Loads config.toml (falls back to config.example.toml defaults) plus env overrides."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import minitoml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    core_dir: Path | None
    core_files: list[str] = field(default_factory=lambda: ["bootstrap.md", "soul.md"])
    context_file: Path = REPO_ROOT / "context" / "the-vault-game.md"
    brain: str = "auto"
    model: str = ""
    tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    claude_path: str = ""
    host: str = "127.0.0.1"
    port: int = 8765


# Where Claude Code is often installed when `claude` is not on the PATH that Python sees.
CLAUDE_CANDIDATES = [
    "~/.claude/local/claude",
    "~/.local/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.npm-global/bin/claude",
    "~/.bun/bin/claude",
]


def find_claude(explicit: str = "") -> str:
    """Path to the Claude Code CLI, or "" if it cannot be found."""
    if explicit:
        p = Path(os.path.expanduser(explicit))
        return str(p) if p.is_file() else ""
    found = shutil.which("claude")
    if found:
        return found
    for c in CLAUDE_CANDIDATES:
        p = Path(os.path.expanduser(c))
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return ""


def _expand(p: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(p)))
    return path if path.is_absolute() else (REPO_ROOT / path)


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("VAULT_CONFIG", REPO_ROOT / "config.toml"))
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = minitoml.load(f)

    tally = data.get("tally", {})
    brain = data.get("brain", {})
    server = data.get("server", {})

    core = os.environ.get("TALLY_CORE_DIR") or tally.get("core_dir")
    cfg = Config(
        core_dir=_expand(core) if core else None,
        core_files=tally.get("core_files", ["bootstrap.md", "soul.md"]),
        context_file=_expand(tally.get("context_file", "context/the-vault-game.md")),
        brain=os.environ.get("TALLY_BRAIN") or brain.get("kind", "auto"),
        model=os.environ.get("TALLY_MODEL") or brain.get("model", ""),
        tools=brain.get("tools", ["Read", "Glob", "Grep"]),
        claude_path=os.environ.get("CLAUDE_PATH") or brain.get("claude_path", ""),
        host=server.get("host", "127.0.0.1"),
        port=int(os.environ.get("VAULT_PORT") or server.get("port", 8765)),
    )
    # Claude Code (your normal login) is the default. The API is only used when asked for.
    if cfg.brain in ("auto", ""):
        cfg.brain = "claude-code"
    cfg.claude_path = find_claude(cfg.claude_path)
    return cfg
