"""Loads config.toml (falls back to config.example.toml defaults) plus env overrides."""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    core_dir: Path | None
    core_files: list[str] = field(default_factory=lambda: ["bootstrap.md", "soul.md"])
    context_file: Path = REPO_ROOT / "context" / "the-vault-game.md"
    brain: str = "auto"
    model: str = ""
    tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    host: str = "127.0.0.1"
    port: int = 8765


def _expand(p: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(p)))
    return path if path.is_absolute() else (REPO_ROOT / path)


def load_config(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("VAULT_CONFIG", REPO_ROOT / "config.toml"))
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

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
        host=server.get("host", "127.0.0.1"),
        port=int(os.environ.get("VAULT_PORT") or server.get("port", 8765)),
    )
    if cfg.brain == "auto":
        cfg.brain = "claude-code" if shutil.which("claude") else "api"
    return cfg
