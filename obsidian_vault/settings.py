"""The menu's Settings window (owner only): the vault folders and the errands browser.

What is set here goes into the local config file at once (config.write_local), outside the repo:
[tally] core_dir (the Private vault), [tally] work_dir (the Work vault, optional) and
[errands] browser. A folder is only accepted if it exists. Paths are shown to the owner only,
and messages never repeat a path: the owner just typed it, and a message may end up in a log.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import BROWSERS, ConfigError, home_form, short_path, write_local


class SettingsError(Exception):
    pass


CORE_FILES_HINT = "it has no bootstrap.md or soul.md, so Tally's core will not load from it"


def view(cfg) -> dict:
    """What the Settings window shows. The owner's own paths: never send this to a guest."""
    def folder(p):
        return {"path": home_form(p) if p else "", "exists": bool(p and Path(p).is_dir())}
    return {
        "core_dir": folder(cfg.core_dir),
        "work_dir": folder(cfg.work_dir),
        "browser": cfg.errand_browser,
        "browsers": list(BROWSERS),
        "config_file": short_path(cfg.config_path) if cfg.config_path else "",
    }


def _folder(raw, label: str, required: bool) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        if required:
            raise SettingsError(f"the {label} folder may not be empty")
        return None
    if len(text) > 1024 or "\x00" in text:
        raise SettingsError(f"that is not a folder path ({label})")
    p = Path(os.path.expandvars(os.path.expanduser(text)))
    if not p.is_absolute():
        raise SettingsError(f"give the {label} folder's full path (it may start with ~/)")
    if not p.is_dir():
        raise SettingsError(f"the {label} folder was not found on this computer")
    return p  # as typed (expanded), not resolved: a symlinked vault keeps its own name


def _same(a: Path | None, b: Path | None) -> bool:
    return (a.resolve() if a else None) == (b.resolve() if b else None)


def save(cfg, data: dict) -> dict:
    """Check what the Settings window sent, write it to the local config, and apply it to cfg.

    Returns {"changed": [...], "notes": [...]}. Everything is checked before anything is written.
    """
    tally, errands, changed, notes = {}, {}, [], []
    if "core_dir" in data:
        p = _folder(data["core_dir"], "Private vault", required=True)
        if not any((p / n).is_file() for n in cfg.core_files):
            notes.append(f"Saved, but {CORE_FILES_HINT}.")
        if not _same(p, cfg.core_dir):
            tally["core_dir"] = home_form(p)
            changed.append("core_dir")
    if "work_dir" in data:
        p = _folder(data["work_dir"], "Work vault", required=False)
        if not _same(p, cfg.work_dir):
            tally["work_dir"] = home_form(p) if p else ""
            changed.append("work_dir")
    if "browser" in data:
        b = str(data["browser"] or "").strip().lower()
        if b not in BROWSERS:
            raise SettingsError(f"the errands browser must be one of {', '.join(BROWSERS)}")
        if b != cfg.errand_browser:
            errands["browser"] = b
            changed.append("browser")
    try:
        if tally:
            write_local(cfg, "tally", tally)
        if errands:
            write_local(cfg, "errands", errands)
    except ConfigError as e:
        raise SettingsError(str(e)) from None
    # the running game takes the new values at once
    if "core_dir" in tally:
        cfg.core_dir = Path(os.path.expanduser(tally["core_dir"]))
    if "work_dir" in tally:
        cfg.work_dir = Path(os.path.expanduser(tally["work_dir"])) if tally["work_dir"] else None
    if "browser" in errands:
        cfg.errand_browser = errands["browser"]
    return {"changed": changed, "notes": notes}
