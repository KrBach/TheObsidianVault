"""Atomic file writes that are safe when two threads save the same file at once.

The game server is threaded, so two requests can save the same state file at the same
moment. Each write goes to its own temp file in the same folder and is then moved into
place, so the last write wins and none of them trips over another's temp file.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def write_text(path: Path | str, text: str, mode: int | None = None) -> None:
    """Write text to path in one step. mode sets the file's permissions; by default a new
    file gets 0o644 and an existing file keeps the permissions it had."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
