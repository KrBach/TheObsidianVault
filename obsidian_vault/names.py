"""Who is who: the owner of the vault and their companion, as set in config.toml [names].

The code and the context files never name a real person. Text meant for the companion or the
screen says OWNER where the owner's name goes (OWNER's for the possessive) and Tally for the
companion; fill() puts the configured names in, at the edges: the system prompt, tool texts,
the tick, errand briefs, and the browser (which gets the names from the server).
"""

from __future__ import annotations

import re

DEFAULT_OWNER = "Owner"
DEFAULT_COMPANION = "Tally"
OWNER_TOKEN = "OWNER"
COMPANION_WORD = "Tally"

_OWNER = re.compile(r"\bOWNER\b")
_COMPANION = re.compile(r"\bTally\b")


def clean_name(name, default: str) -> str:
    name = " ".join(str(name or "").split())[:40]
    return name if name and not any(c in name for c in "<>{}\"`") else default


def fill(text: str, owner: str = DEFAULT_OWNER, companion: str = DEFAULT_COMPANION) -> str:
    """Put the configured names into text written with OWNER and Tally."""
    if not text:
        return text
    text = _OWNER.sub(lambda m: owner, text)
    if companion != COMPANION_WORD:
        text = _COMPANION.sub(lambda m: companion, text)
    return text


def local_now(tzname: str = ""):
    """Now, in the configured time zone (an IANA name), or this computer's when none is set."""
    from datetime import datetime
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tzname))
        except Exception:  # no tz database, or a name it does not know
            pass
    return datetime.now().astimezone()


def tz_label(tzname: str = "") -> str:
    return tzname or (local_now().tzname() or "local time")
