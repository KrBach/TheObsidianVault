"""Character settings changed from inside the game: names, time zone and the owner's portrait.

config.toml stays yours to edit by hand (with its comments); the game never rewrites it. What the
owner sets in the game's Character window is kept in <state_dir>/settings.json and wins over
config.toml. An uploaded portrait is kept in <state_dir>/portrait.<ext> and served at /portrait/owner.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from .names import DEFAULT_COMPANION, DEFAULT_OWNER, clean_name

SETTINGS = "settings.json"
PORTRAIT_URL = "portrait/owner"
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAGIC = {"png": b"\x89PNG", "jpg": b"\xff\xd8\xff", "webp": b"RIFF"}
MAX_PORTRAIT = 3 * 1024 * 1024
TZ_RE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$")


class CharacterError(Exception):
    pass


def read_settings(state_dir: Path) -> dict:
    try:
        data = json.loads((Path(state_dir) / SETTINGS).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_settings(cfg) -> None:
    """Lay settings.json over the config (load_config calls this, so every process sees the same)."""
    s = read_settings(cfg.state_dir)
    if s.get("owner"):
        cfg.owner = clean_name(s["owner"], cfg.owner)
    if s.get("companion"):
        cfg.companion = clean_name(s["companion"], cfg.companion)
    if "timezone" in s:
        cfg.timezone = str(s["timezone"])
    if s.get("portrait") and portrait_file(cfg.state_dir):
        cfg.owner_portrait = PORTRAIT_URL


def portrait_file(state_dir: Path) -> Path | None:
    for ext in ("png", "jpg", "webp"):
        p = Path(state_dir) / f"portrait.{ext}"
        if p.is_file():
            return p
    return None


def valid_timezone(name: str) -> bool:
    if not name:
        return True
    if not TZ_RE.match(name):
        return False
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except ImportError:  # pragma: no cover - very old Python: trust the shape
        return True
    except Exception:
        return False


def save(cfg, data: dict) -> dict:
    """Check and keep what the Character window sent. Returns the settings now in force."""
    s = read_settings(cfg.state_dir)
    if "owner" in data:
        owner = clean_name(data["owner"], "")
        if not owner:
            raise CharacterError("your name may not be empty or contain < > { } \" or `")
        s["owner"] = owner
    if "companion" in data:
        companion = clean_name(data["companion"], "")
        if not companion:
            raise CharacterError("the companion's name may not be empty or contain < > { } \" or `")
        s["companion"] = companion
    if "timezone" in data:
        tz = str(data["timezone"] or "").strip()
        if not valid_timezone(tz):
            raise CharacterError(f"'{tz[:40]}' is not a time zone this computer knows (try e.g. Europe/Berlin)")
        s["timezone"] = tz
    if data.get("portrait"):
        _save_portrait(cfg.state_dir, str(data["portrait"]))
        s["portrait"] = True
    elif data.get("portrait") == "" and data.get("remove_portrait"):
        for ext in ("png", "jpg", "webp"):
            (Path(cfg.state_dir) / f"portrait.{ext}").unlink(missing_ok=True)
        s["portrait"] = False
    Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
    tmp = Path(cfg.state_dir) / f".{SETTINGS}.tmp"
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, Path(cfg.state_dir) / SETTINGS)
    # the running game takes the new values at once
    cfg.owner = s.get("owner", cfg.owner or DEFAULT_OWNER)
    cfg.companion = s.get("companion", cfg.companion or DEFAULT_COMPANION)
    cfg.timezone = s.get("timezone", cfg.timezone)
    if s.get("portrait"):
        cfg.owner_portrait = PORTRAIT_URL
    elif s.get("portrait") is False and cfg.owner_portrait == PORTRAIT_URL:
        cfg.owner_portrait = ""
    return s


def _save_portrait(state_dir: Path, data_url: str) -> None:
    m = re.match(r"^data:(image/(?:png|jpeg|webp));base64,(.+)$", data_url, re.S)
    if not m:
        raise CharacterError("the portrait must be a PNG, JPEG or WebP image")
    ext = IMAGE_TYPES[m.group(1)]
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise CharacterError("the portrait could not be read")
    if len(raw) > MAX_PORTRAIT:
        raise CharacterError("the portrait is over 3 MB; use a smaller image")
    if not raw.startswith(MAGIC[ext]):
        raise CharacterError("that file is not the image it claims to be")
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    for old in ("png", "jpg", "webp"):
        (Path(state_dir) / f"portrait.{old}").unlink(missing_ok=True)
    (Path(state_dir) / f"portrait.{ext}").write_bytes(raw)
