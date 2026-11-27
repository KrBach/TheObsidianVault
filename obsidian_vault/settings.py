"""The menu's Settings window (owner only): the vault folders, the errands browser, the idle time
and the usage limits.

What is set here goes into the local config file at once (config.write_local), outside the repo:
[tally] core_dir (the Private vault), [tally] work_dir (the Work vault, optional),
[tally] idle_minutes (when an idle conversation closes), [errands] browser, and [usage] block_budget
and daily_cap. A folder is only accepted if it exists. Paths are shown to the owner only,
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
        "block_budget": cfg.usage_block_budget,
        "daily_cap": cfg.usage_daily_cap,
        "idle_minutes": cfg.idle_minutes,
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
    usage = {}
    for key, attr, label in (("block_budget", "usage_block_budget", "block budget"),
                             ("daily_cap", "usage_daily_cap", "daily cap")):
        if key not in data:
            continue
        try:
            v = round(float(data[key]), 2)
        except (TypeError, ValueError):
            raise SettingsError(f"the {label} must be a number of dollars")
        if not 0 <= v <= 1000:
            raise SettingsError(f"the {label} must be between $0 and $1000")
        if v != getattr(cfg, attr):
            usage[key] = v
            changed.append(key)
    if "idle_minutes" in data:
        try:
            m = float(data["idle_minutes"])
        except (TypeError, ValueError):
            raise SettingsError("the idle time must be a number of minutes (0: never)") from None
        if not 0 <= m <= 24 * 60:
            raise SettingsError("the idle time must be between 0 and 1440 minutes")
        m = int(m) if m == int(m) else m
        if m != cfg.idle_minutes:
            tally["idle_minutes"] = m
            changed.append("idle_minutes")
    try:
        if tally:
            write_local(cfg, "tally", tally)
        if errands:
            write_local(cfg, "errands", errands)
        if usage:
            write_local(cfg, "usage", usage)
    except ConfigError as e:
        raise SettingsError(str(e)) from None
    # the running game takes the new values at once
    if "core_dir" in tally:
        cfg.core_dir = Path(os.path.expanduser(tally["core_dir"]))
    if "work_dir" in tally:
        cfg.work_dir = Path(os.path.expanduser(tally["work_dir"])) if tally["work_dir"] else None
    if "browser" in errands:
        cfg.errand_browser = errands["browser"]
    if "block_budget" in usage:
        cfg.usage_block_budget = usage["block_budget"]
    if "daily_cap" in usage:
        cfg.usage_daily_cap = usage["daily_cap"]
    if "idle_minutes" in tally:
        cfg.idle_minutes = float(tally["idle_minutes"])
    return {"changed": changed, "notes": notes}
