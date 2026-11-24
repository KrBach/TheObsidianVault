"""Tally's writes to the vault: small, checked, reversible.

Tally never edits files freely. It asks for one of five changes:

  append_log      one new line at the end of soul.md's "Interaction log"
  add_coaching    one new item under soul.md's "For the coaching thread"
  edit_soul       replace one exact passage in soul.md
  propose_edit    replace one exact passage in an existing note
  propose_note    create a new note

Each change is either applied at once or queued for Kristian to approve in the game,
depending on writes.mode in config.toml:

  off      nothing can be written (the default before this feature)
  review   note edits and new notes wait for approval; soul.md changes are written at once,
           because soul.md is Tally's own (set writes.soul_direct = false to review those too)
  direct   the same as review with soul_direct on

Rules that always hold:
- only .md files inside the vault folder; never bootstrap.md, archive/, templates/ or
  anything hidden (.obsidian/ and friends);
- note edits and new notes only in the allowed theme folders (writes.folders);
- an edit is applied only if the note has not changed since Tally proposed it;
  appends re-read the file just before writing, as bootstrap.md asks;
- every write keeps a backup of the previous file and a journal line, so it can be undone.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from .config import Config

SOUL = "soul.md"
LOG_SECTION = "## Interaction log"
COACHING_SECTION = "## For the coaching thread"
PROJECT_NAME = "The Obsidian Vault"
NEVER = {"bootstrap.md"}
NEVER_DIRS = {"archive", "templates"}
WARM_FILES = ("bootstrap.md", "soul.md")
WARM_GUIDE_KB = 35
MAX_LINE = 2000


class LedgerError(Exception):
    """A change that is not allowed or no longer applies. The message is shown to Tally."""


@dataclass
class Result:
    status: str  # "applied" | "queued"
    message: str
    id: str | None = None


def _now() -> datetime:
    tz = ZoneInfo("Europe/Copenhagen") if ZoneInfo else None
    try:
        return datetime.now(tz)
    except Exception:  # no tz database on this machine
        return datetime.now()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _one_line(text: str, what: str) -> str:
    text = " ".join(str(text).split())
    if not text:
        raise LedgerError(f"{what} is empty")
    if len(text) > MAX_LINE:
        raise LedgerError(f"{what} is {len(text)} characters; keep it under {MAX_LINE}")
    return text


def insert_in_section(doc: str, section: str, line: str, before: str | None = None) -> str:
    """Insert `line` after the last bullet of `section` (or before the bullet `before`)."""
    lines = doc.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == section)
    except StopIteration:
        raise LedgerError(f'soul.md has no "{section}" section')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    if before:
        for i in range(start + 1, end):
            if lines[i].strip() == before:
                lines.insert(i, line)
                return "\n".join(lines)
    bullets = [i for i in range(start + 1, end) if lines[i].startswith("- ")]
    at = (bullets[-1] + 1) if bullets else start + 1
    lines.insert(at, line)
    return "\n".join(lines)


class Ledger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.core_dir.resolve() if cfg.core_dir else None
        self.mode = cfg.writes_mode
        self.state = cfg.state_dir
        self.pending_dir = self.state / "pending"
        self.backup_dir = self.state / "backups"
        self.journal = self.state / "journal.jsonl"

    # ---------- paths ----------

    def resolve(self, rel: str, *, note: bool) -> Path:
        """Vault-relative path -> absolute path, or LedgerError if not allowed."""
        if not self.root or not self.root.is_dir():
            raise LedgerError("the vault folder is not set or not found")
        rel = str(rel).strip().lstrip("/")
        p = (self.root / rel).resolve()
        if self.root not in p.parents:
            raise LedgerError(f"{rel} is outside the vault")
        parts = p.relative_to(self.root).parts
        if p.suffix != ".md":
            raise LedgerError("only .md notes can be written")
        if any(part.startswith(".") or part.startswith("_") for part in parts):
            raise LedgerError(f"{rel} is in a hidden folder")
        if p.name in NEVER and len(parts) == 1:
            raise LedgerError(f"{rel} is never written from the game")
        if parts[0] in NEVER_DIRS:
            raise LedgerError(f"{parts[0]}/ is never written from the game")
        if note and (len(parts) < 2 or parts[0] not in self.cfg.writes_folders):
            allowed = ", ".join(f"{f}/" for f in self.cfg.writes_folders)
            raise LedgerError(f"notes can only be written in: {allowed}")
        return p

    def soul(self) -> Path:
        if not self.root:
            raise LedgerError("the vault folder is not set")
        return self.root / SOUL

    # ---------- requests from Tally ----------

    def append_log(self, theme: str, entry: str, theme_link: str = "") -> Result:
        theme = _one_line(theme, "theme")
        entry = _one_line(entry, "entry")
        if theme_link:
            self.resolve(theme_link, note=False)  # must be a real-looking vault path
            theme = f"{theme} ([note]({theme_link}))"
        stamp = _now().strftime("%Y-%m-%d %H:%M")
        line = f"- {stamp} (Europe/Copenhagen), Project: {PROJECT_NAME} · Theme: {theme}: {entry}"
        op = {"kind": "append", "file": SOUL, "section": LOG_SECTION, "line": line,
              "summary": "Add a line to the interaction log"}
        return self._submit(op, direct_ok=True)

    def add_coaching(self, item: str) -> Result:
        item = _one_line(item, "item")
        line = f"- {item} (from {PROJECT_NAME}, {_now():%Y-%m-%d})"
        op = {"kind": "append", "file": SOUL, "section": COACHING_SECTION, "line": line,
              "before": "- (nothing else open)", "summary": "Add an item for the coaching thread"}
        return self._submit(op, direct_ok=True)

    def edit_soul(self, old: str, new: str, reason: str) -> Result:
        p = self.soul()
        if not p.is_file():
            raise LedgerError("soul.md is missing")
        text = p.read_text(encoding="utf-8")
        if not old:
            raise LedgerError("old text is empty")
        count = text.count(old)
        if count != 1:
            raise LedgerError(f"the old text occurs {count} times in soul.md; it must occur exactly once")
        op = {"kind": "replace", "file": SOUL, "old": old, "new": new, "sha": _sha(text),
              "summary": _one_line(reason, "reason")}
        return self._submit(op, direct_ok=True)

    def propose_edit(self, path: str, old: str, new: str, reason: str) -> Result:
        p = self.resolve(path, note=True)
        if not p.is_file():
            raise LedgerError(f"{path} does not exist; use propose_note to create it")
        text = p.read_text(encoding="utf-8")
        if not old:
            raise LedgerError("old text is empty")
        count = text.count(old)
        if count != 1:
            raise LedgerError(f"the old text occurs {count} times in {path}; it must occur exactly once")
        op = {"kind": "replace", "file": str(p.relative_to(self.root)), "old": old, "new": new,
              "sha": _sha(text), "summary": _one_line(reason, "reason")}
        return self._submit(op, direct_ok=False)

    def propose_note(self, path: str, content: str, reason: str) -> Result:
        p = self.resolve(path, note=True)
        if p.exists():
            raise LedgerError(f"{path} already exists; use propose_edit to change it")
        if not content.strip():
            raise LedgerError("the note is empty")
        op = {"kind": "create", "file": str(p.relative_to(self.root)), "content": content,
              "summary": _one_line(reason, "reason")}
        return self._submit(op, direct_ok=False)

    # ---------- queue ----------

    def _submit(self, op: dict, direct_ok: bool) -> Result:
        if self.mode == "off":
            raise LedgerError("writing is switched off (writes.mode = \"off\" in config.toml)")
        if direct_ok and (self.mode == "direct" or (self.mode == "review" and self.cfg.soul_direct)):
            self.apply(op)
            return Result("applied", f"Written to {op['file']}. {self.size_report()}")
        op = {**op, "id": uuid.uuid4().hex[:10], "created": time.time()}
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        (self.pending_dir / f"{op['id']}.json").write_text(json.dumps(op, ensure_ascii=False), encoding="utf-8")
        return Result("queued", f"Queued for Kristian's review in the game (id {op['id']}). Nothing is written until he approves it.", op["id"])

    def pending(self) -> list[dict]:
        if not self.pending_dir.is_dir():
            return []
        ops = []
        for f in sorted(self.pending_dir.glob("*.json"), key=lambda f: f.stat().st_mtime):
            op = json.loads(f.read_text(encoding="utf-8"))
            try:
                before, after = self.preview(op)
                op["diff"] = "".join(difflib.unified_diff(
                    before.splitlines(True), after.splitlines(True), f"a/{op['file']}", f"b/{op['file']}", n=1))
                op["stale"] = False
            except LedgerError as e:
                op["diff"], op["stale"] = str(e), True
            ops.append(op)
        return ops

    def _take(self, op_id: str) -> tuple[Path, dict]:
        f = self.pending_dir / f"{''.join(c for c in op_id if c.isalnum())}.json"
        if not f.is_file():
            raise LedgerError("that change is no longer pending")
        return f, json.loads(f.read_text(encoding="utf-8"))

    def approve(self, op_id: str) -> str:
        f, op = self._take(op_id)
        self.apply(op)
        f.unlink()
        return f"Written to {op['file']}. {self.size_report(for_tally=False)}"

    def reject(self, op_id: str) -> None:
        f, op = self._take(op_id)
        f.unlink()
        self._log({"event": "rejected", "file": op["file"], "summary": op.get("summary")})

    # ---------- applying ----------

    def preview(self, op: dict) -> tuple[str, str]:
        """(current text, text after the change). Raises LedgerError if it no longer applies."""
        p = self.resolve(op["file"], note=op["kind"] != "append") if op["file"] != SOUL else self.soul()
        if op["kind"] == "create":
            if p.exists():
                raise LedgerError(f"{op['file']} was created by someone else in the meantime")
            return "", op["content"]
        if not p.is_file():
            raise LedgerError(f"{op['file']} is missing")
        text = p.read_text(encoding="utf-8")  # re-read just before writing
        if op["kind"] == "append":
            return text, insert_in_section(text, op["section"], op["line"], op.get("before"))
        if op["kind"] == "replace":
            if _sha(text) != op["sha"]:
                raise LedgerError(f"{op['file']} changed since this edit was proposed; ask Tally again")
            return text, text.replace(op["old"], op["new"], 1)
        raise LedgerError(f"unknown change {op['kind']}")

    def apply(self, op: dict) -> None:
        before, after = self.preview(op)
        p = self.soul() if op["file"] == SOUL else self.resolve(op["file"], note=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = None
        if p.exists():
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup = self.backup_dir / f"{stamp}-{op['file'].replace('/', '__')}"
            backup.write_text(before, encoding="utf-8")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.vault-tmp")
        tmp.write_text(after, encoding="utf-8")
        os.replace(tmp, p)
        self._log({"event": "written", "file": op["file"], "kind": op["kind"], "summary": op.get("summary"),
                   "backup": str(backup) if backup else None})

    def _log(self, rec: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        rec = {"time": _now().isoformat(timespec="seconds"), **rec}
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def size_report(self, for_tally: bool = True) -> str:
        if not self.root:
            return ""
        total = sum((self.root / n).stat().st_size for n in WARM_FILES if (self.root / n).is_file())
        kb = total / 1024
        if kb <= WARM_GUIDE_KB:
            note = ""
        elif for_tally:
            note = " That is over the guide; mention it so Kristian can plan an archive pass."
        else:
            note = " That is over the guide; an archive pass is due."
        return f"Warm files (bootstrap.md + soul.md) are now {kb:.1f} KB; the guide is about {WARM_GUIDE_KB} KB.{note}"
