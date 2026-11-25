"""Loads config.toml (falls back to config.example.toml defaults) plus env overrides."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import minitoml

REPO_ROOT = Path(__file__).resolve().parent.parent


DEFAULT_FOLDERS = ["games", "worldbuilding", "ai-risk", "obsidian", "experiments"]
WRITE_MODES = ("off", "review", "direct")
# Tools a "session" errand may use without asking: your own Claude Code setup, skills and browser.
DEFAULT_SESSION_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill", "WebSearch", "WebFetch",
                         "mcp__claude-in-chrome"]


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
    writes_mode: str = "review"
    writes_folders: list[str] = field(default_factory=lambda: list(DEFAULT_FOLDERS))
    soul_direct: bool = True  # soul.md is Tally's own: their lines and edits go in without review
    state_dir: Path = REPO_ROOT / ".vault-state"
    config_path: Path | None = None
    tick_minutes: float = 6.0
    tick_model: str = "haiku"
    work_dir: Path | None = None  # the Work vault: read-only from the game
    web: bool = True  # WebSearch and WebFetch (read-only lookups)
    # Errands: bigger jobs Tally queues in the ledger; approving one starts a separate Claude Code run.
    errands: bool = True
    errand_timeout_minutes: float = 45.0
    errand_max_running: int = 2
    errand_model: str = ""
    errand_repo: Path = REPO_ROOT  # where "code" errands branch from (in a git worktree)
    errand_worktrees: Path = REPO_ROOT.parent / "TheObsidianVault-errands"
    errand_session_tools: list[str] = field(default_factory=lambda: list(DEFAULT_SESSION_TOOLS))
    # The notice board: what the game may show. Vault folders (relative to core_dir), read-only.
    board_folders: list[str] = field(default_factory=lambda: ["Claude outputs"])


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
    writes = data.get("writes", {})

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
        writes_mode=str(os.environ.get("VAULT_WRITES") or writes.get("mode", "review")).lower(),
        writes_folders=list(writes.get("folders", DEFAULT_FOLDERS)),
        soul_direct=bool(writes.get("soul_direct", True)),
        state_dir=_expand(os.environ.get("VAULT_STATE_DIR") or writes.get("state_dir", ".vault-state")),
        config_path=path.resolve() if path.exists() else None,
        tick_minutes=float(os.environ.get("VAULT_TICK_MINUTES") or tally.get("tick_minutes", 6)),
        tick_model=str(tally.get("tick_model", "haiku")),
        web=bool(brain.get("web", True)),
    )
    errands = data.get("errands", {})
    cfg.errands = bool(errands.get("enabled", True))
    cfg.errand_timeout_minutes = float(errands.get("timeout_minutes", 45))
    cfg.errand_max_running = int(errands.get("max_running", 2))
    cfg.errand_model = str(errands.get("model", ""))
    if errands.get("repo"):
        cfg.errand_repo = _expand(errands["repo"])
    if errands.get("worktrees"):
        cfg.errand_worktrees = _expand(errands["worktrees"])
    cfg.errand_session_tools = list(errands.get("session_tools", DEFAULT_SESSION_TOOLS))
    cfg.board_folders = list(data.get("board", {}).get("folders", ["Claude outputs"]))
    work = os.environ.get("TALLY_WORK_DIR") or tally.get("work_dir")
    if work:
        cfg.work_dir = _expand(work)
    if cfg.writes_mode not in WRITE_MODES:
        print(f'note: writes.mode "{cfg.writes_mode}" is not one of {", ".join(WRITE_MODES)}; using "off".')
        cfg.writes_mode = "off"
    # Claude Code (your normal login) is the default. The API is only used when asked for.
    if cfg.brain in ("auto", ""):
        cfg.brain = "claude-code"
    # The vault tools are an MCP server that Claude Code starts; the API brain has no way to call them.
    if cfg.brain != "claude-code":
        cfg.writes_mode = "off"
    cfg.claude_path = find_claude(cfg.claude_path)
    return cfg
