"""Character settings changed from inside the game: names, time zone and the owner's portrait.

Names and the time zone go straight into the local config file (config.write_local: [names] owner
and companion, [tally] timezone), the same file you can edit by hand, outside the repo, so an update
never loses them. Its comments are kept. An uploaded portrait is kept in <state_dir>/portrait.<ext>,
served at /portrait/owner, and wins over [names] owner_portrait while it is there.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from .config import ConfigError, write_local
from .names import clean_name

PORTRAIT_URL = "portrait/owner"
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAGIC = {"png": b"\x89PNG", "jpg": b"\xff\xd8\xff", "webp": b"RIFF"}
MAX_PORTRAIT = 3 * 1024 * 1024
TZ_RE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$")


class CharacterError(Exception):
    pass


def apply_portrait(cfg) -> None:
    """An uploaded portrait wins over [names] owner_portrait (load_config calls this)."""
    if portrait_file(cfg.state_dir):
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
    """Check what the Character window sent and write it to the local config at once.

    Everything is checked before anything is written, so a bad field changes nothing.
    Returns what was set.
    """
    names, tally = {}, {}
    if "owner" in data:
        owner = clean_name(data["owner"], "")
        if not owner:
            raise CharacterError("your name may not be empty or contain < > { } \" or `")
        names["owner"] = owner
    if "companion" in data:
        companion = clean_name(data["companion"], "")
        if not companion:
            raise CharacterError("the companion's name may not be empty or contain < > { } \" or `")
        names["companion"] = companion
    if "timezone" in data:
        tz = str(data["timezone"] or "").strip()
        if not valid_timezone(tz):
            raise CharacterError(f"'{tz[:40]}' is not a time zone this computer knows (try e.g. Europe/Berlin)")
        tally["timezone"] = tz
    try:
        if names:
            write_local(cfg, "names", names)
        if tally:
            write_local(cfg, "tally", tally)
    except ConfigError as e:
        raise CharacterError(str(e)) from None
    if data.get("portrait"):
        _save_portrait(cfg.state_dir, str(data["portrait"]))
    elif data.get("portrait") == "" and data.get("remove_portrait"):
        for ext in ("png", "jpg", "webp"):
            (Path(cfg.state_dir) / f"portrait.{ext}").unlink(missing_ok=True)
    # the running game takes the new values at once
    cfg.owner = names.get("owner", cfg.owner)
    cfg.companion = names.get("companion", cfg.companion)
    cfg.timezone = tally.get("timezone", cfg.timezone)
    if portrait_file(cfg.state_dir):
        cfg.owner_portrait = PORTRAIT_URL
    elif cfg.owner_portrait == PORTRAIT_URL:
        cfg.owner_portrait = ""
    return {**names, **tally}


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
