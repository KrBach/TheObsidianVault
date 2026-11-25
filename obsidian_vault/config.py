"""Loads the local config.toml plus env overrides.

Everything that belongs to one computer or one person (the vault folders, the owner's name, the time
zone, the errands browser) lives in ONE local file outside the repo, so a pull, a fresh download or a
new release never touches it:

  macOS    ~/Library/Application Support/TheObsidianVault/config.toml
  Linux    $XDG_CONFIG_HOME/TheObsidianVault/config.toml (default ~/.config/...)
  Windows  %APPDATA%\\TheObsidianVault\\config.toml

VAULT_CONFIG=<file> points somewhere else. On first run the file is created: moved in from an old
config.toml in the game folder if there is one, otherwise from config.example.toml with the
placeholders left empty. The game's menu writes to it (write_local), keeping its comments.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import minitoml

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR_NAME = "TheObsidianVault"
EXAMPLE_CONFIG = REPO_ROOT / "config.example.toml"
LEGACY_CONFIG = REPO_ROOT / "config.toml"  # where config.toml lived before it moved out of the repo
LEGACY_SETTINGS = "settings.json"  # what the Character window kept in the state folder before
# The browsers a session errand can have ([errands] browser).
BROWSERS = ("safari", "chrome", "off")


class ConfigError(Exception):
    pass


def user_config_dir() -> Path:
    """The per-user folder for the local config, outside the repo and outside any release."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / APP_DIR_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / APP_DIR_NAME


def local_config_path() -> Path:
    env = os.environ.get("VAULT_CONFIG")
    return Path(os.path.expanduser(env)) if env else user_config_dir() / "config.toml"


def short_path(p) -> str:
    """A path for messages: the home folder as ~ and the middle left out, so no folder names leak."""
    if not p:
        return "(not set)"
    p = Path(p)
    try:
        parts = ("~",) + p.relative_to(Path.home()).parts
    except ValueError:
        parts = p.parts
    return "/".join(parts) if len(parts) <= 2 else f"{parts[0].rstrip('/') or ''}/…/{parts[-1]}"


def home_form(p: Path) -> str:
    """A path as it is written into the config: ~/... when it is under the home folder."""
    try:
        return "~/" + p.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(p)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fresh_config_text() -> str:
    """config.example.toml with its placeholders emptied, as the start of a new local config."""
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8") if EXAMPLE_CONFIG.is_file() else ""
    head, sep, rest = text.partition("\n\n")
    if head.startswith("#"):  # the example's header says how to copy it; this is the copy
        text = ("# The Obsidian Vault: your local settings. This file is yours and lives outside the game's\n"
                "# folder, so updates never touch it. The game's menu (Esc) writes to it and keeps these comments.\n"
                + sep + rest)
    text = minitoml.set_values(text, "names", {"owner": ""})
    return minitoml.set_values(text, "tally", {"core_dir": ""})


def ensure_local_config(path: Path) -> str:
    """Create the local config on first run. Returns a line for the terminal, or "" if nothing happened."""
    if path.is_file():
        if LEGACY_CONFIG.is_file():
            return ("note: there is an old config.toml in the game folder; it is no longer read. "
                    f"Your settings are in {short_path(path)}.")
        return ""
    if LEGACY_CONFIG.is_file():  # once: the old file moves out of the game folder, nothing in it is lost
        _write_atomic(path, LEGACY_CONFIG.read_text(encoding="utf-8"))
        try:
            LEGACY_CONFIG.rename(LEGACY_CONFIG.with_name("config.toml.migrated"))
        except OSError:
            pass
        return f"moved config.toml out of the game folder to {short_path(path)}"
    _write_atomic(path, fresh_config_text())
    return f"created your local settings in {short_path(path)} (set the vault folder in the game: Esc → Settings)"


def migrate_settings(state_dir: Path, path: Path) -> bool:
    """Once: fold what the Character window kept in <state_dir>/settings.json into the local config.

    Those values won over config.toml before, so they win here too. settings.json is renamed, not
    deleted, so nothing is lost and it is not read again.
    """
    old = Path(state_dir) / LEGACY_SETTINGS
    if not old.is_file() or not path.is_file():
        return False
    try:
        s = json.loads(old.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    s = s if isinstance(s, dict) else {}
    text = path.read_text(encoding="utf-8")
    names = {k: str(s[k]) for k in ("owner", "companion") if s.get(k)}
    if names:
        text = minitoml.set_values(text, "names", names)
    if "timezone" in s:
        text = minitoml.set_values(text, "tally", {"timezone": str(s["timezone"] or "")})
    _write_atomic(path, text)
    try:
        old.rename(old.with_name(LEGACY_SETTINGS + ".migrated"))
    except OSError:
        pass
    return True


def write_local(cfg: "Config", section: str, values: dict) -> None:
    """Write settings to the local config at once, keeping its comments and every other line."""
    if not cfg.config_path:
        raise ConfigError("there is no local config file to save to")
    path = Path(cfg.config_path)
    text = path.read_text(encoding="utf-8") if path.is_file() else fresh_config_text()
    try:
        _write_atomic(path, minitoml.set_values(text, section, values))
    except OSError as e:
        raise ConfigError(f"could not save the settings ({e.strerror or 'write failed'})") from None


DEFAULT_FOLDERS = ["games", "worldbuilding", "ai-risk", "obsidian", "experiments", "denizens"]
WRITE_MODES = ("off", "review", "direct")
# Tools a "session" errand may use without asking: your own Claude Code setup, skills and browser.
DEFAULT_SESSION_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill", "WebSearch", "WebFetch",
                         "mcp__mcp-safari"]
# The process each errand browser needs running (BROWSERS is above).
BROWSER_APPS = {"safari": "Safari", "chrome": "Google Chrome"}


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
    thoughts_per_day: int = 3  # think-harder handoffs a tick may start on its own (0: off)
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
    # The browser a session errand gets: "safari" (MCPSafari, the mcp-safari server), "chrome" (Claude in
    # Chrome, started with `claude --chrome`) or "off". The desktop app's built-in browser cannot reach a
    # run the game starts.
    errand_browser: str = "safari"
    # The process that must be running before a session errand starts; "" = the browser's usual name.
    errand_browser_app: str = ""
    # The notice board: what the game may show. Vault folders (relative to core_dir), read-only.
    board_folders: list[str] = field(default_factory=lambda: ["Claude outputs"])
    # Who is who ([names]): the vault's owner and their companion. Nothing personal lives in the code.
    owner: str = "Owner"
    companion: str = "Tally"
    owner_portrait: str = ""  # an image under web/, e.g. "portraits/me.jpg"; empty = a painted stand-in
    timezone: str = ""  # an IANA name such as "Europe/Berlin" for log timestamps; empty = this computer's

    def fill(self, text: str) -> str:
        """Text written with OWNER and Tally, with this vault's names in."""
        from .names import fill
        return fill(text, self.owner, self.companion)


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


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return minitoml.load(f)


def load_config(path: Path | None = None) -> Config:
    notes = []
    # Only the game's own load (no path given) creates or migrates anything; a load with an explicit
    # path (tests, tools) only reads, so it can never move the running game's files.
    migrate = path is None
    if path is None:
        path = local_config_path()
        if not os.environ.get("VAULT_CONFIG"):  # the per-user file: make it on first run
            try:
                note = ensure_local_config(path)
            except OSError as e:
                note = f"note: could not create your local settings ({e.strerror or 'write failed'}); using defaults"
            if note:
                notes.append(note)
    data = _read(path)

    tally = data.get("tally", {})
    brain = data.get("brain", {})
    server = data.get("server", {})
    writes = data.get("writes", {})
    state_dir = _expand(os.environ.get("VAULT_STATE_DIR") or writes.get("state_dir", ".vault-state"))
    try:
        if migrate and migrate_settings(state_dir, path):  # once: names and time zone the menu kept in settings.json
            notes.append(f"moved the names and time zone set in the game into {short_path(path)}")
            data = _read(path)
            tally = data.get("tally", {})
    except OSError:
        pass

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
        state_dir=state_dir,
        config_path=path.resolve(),
        tick_minutes=float(os.environ.get("VAULT_TICK_MINUTES") or tally.get("tick_minutes", 6)),
        tick_model=str(tally.get("tick_model", "haiku")),
        thoughts_per_day=int(tally.get("thoughts_per_day", 3)),
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
    browser = str(errands.get("browser", "safari")).strip().lower() or "off"
    if browser not in BROWSERS:
        notes.append(f'note: errands.browser "{browser[:20]}" is not one of {", ".join(BROWSERS)}; using "off".')
        browser = "off"
    cfg.errand_browser = browser
    cfg.errand_browser_app = str(errands.get("browser_app", "")).strip()
    cfg.board_folders = list(data.get("board", {}).get("folders", ["Claude outputs"]))
    from .names import DEFAULT_COMPANION, DEFAULT_OWNER, clean_name
    names = data.get("names", {})
    cfg.owner = clean_name(os.environ.get("VAULT_OWNER") or names.get("owner"), DEFAULT_OWNER)
    cfg.companion = clean_name(names.get("companion"), DEFAULT_COMPANION)
    cfg.owner_portrait = str(names.get("owner_portrait", "")).strip()
    cfg.timezone = str(tally.get("timezone", "")).strip()
    from .character import apply_portrait
    apply_portrait(cfg)  # a portrait uploaded in the game's Character window wins over owner_portrait
    work = os.environ.get("TALLY_WORK_DIR") or tally.get("work_dir")
    if work:
        cfg.work_dir = _expand(work)
    if cfg.writes_mode not in WRITE_MODES:
        notes.append(f'note: writes.mode "{cfg.writes_mode[:20]}" is not one of {", ".join(WRITE_MODES)}; using "off".')
        cfg.writes_mode = "off"
    for note in notes:  # the terminal only; stderr, so the vault tools' MCP channel on stdout stays clean
        print(note, file=sys.stderr)
    # Claude Code (your normal login) is the default. The API is only used when asked for.
    if cfg.brain in ("auto", ""):
        cfg.brain = "claude-code"
    # The vault tools are an MCP server that Claude Code starts; the API brain has no way to call them.
    if cfg.brain != "claude-code":
        cfg.writes_mode = "off"
    cfg.claude_path = find_claude(cfg.claude_path)
    return cfg
