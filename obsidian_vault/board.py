"""The notice board: things to look at, shown inside the game.

Three sources, all read-only from the game:
- files in the vault folders named in [board] folders (default "Claude outputs"): Markdown, images, HTML;
- errand reports (errands.py);
- links Tally pins (pin_to_board), such as a claude.ai artifact.

Files are served only from inside those folders, by an id the board hands out, never by a path the
browser chooses.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import Config

KINDS = {".md": "note", ".txt": "note", ".png": "image", ".jpg": "image", ".jpeg": "image",
         ".gif": "image", ".webp": "image", ".svg": "image", ".html": "page", ".htm": "page"}
TYPES = {".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
         ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8"}
MAX_FILES = 200


class BoardError(Exception):
    pass


def _fid(rel: str) -> str:
    return "f" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]


class Board:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pins_file = cfg.state_dir / "board.json"

    # ---------- pins ----------

    def pins(self) -> list[dict]:
        try:
            return json.loads(self.pins_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def pin(self, title: str, url: str, note: str = "") -> str:
        title = " ".join(str(title).split())[:120]
        url = str(url).strip()
        if not title:
            raise BoardError("the pin needs a title")
        u = urlparse(url)
        if u.scheme != "https" or not u.netloc:
            raise BoardError("only https links can be pinned")
        pins = [p for p in self.pins() if p["url"] != url]
        pins.insert(0, {"id": "p" + hashlib.sha256(url.encode()).hexdigest()[:12], "title": title,
                        "url": url, "note": " ".join(str(note).split())[:300], "pinned": time.time()})
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.pins_file.write_text(json.dumps(pins[:50], ensure_ascii=False, indent=1), encoding="utf-8")
        return f"Pinned to the notice board: {title}."

    def unpin(self, url_or_id: str) -> str:
        pins = self.pins()
        keep = [p for p in pins if url_or_id not in (p["url"], p["id"])]
        if len(keep) == len(pins):
            raise BoardError("no such pin")
        self.pins_file.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
        return "Taken down from the notice board."

    # ---------- files ----------

    def _roots(self) -> list[tuple[str, Path]]:
        root = self.cfg.core_dir
        if not root or not root.is_dir():
            return []
        out = []
        for folder in self.cfg.board_folders:
            p = (root / folder).resolve()
            if p.is_dir() and (p == root.resolve() or root.resolve() in p.parents):
                out.append((folder, p))
        return out

    def files(self) -> list[dict]:
        found = []
        for folder, base in self._roots():
            for f in base.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in KINDS:
                    continue
                rel = f.relative_to(base)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                found.append({"id": _fid(f"{folder}/{rel.as_posix()}"), "title": f.stem.replace("-", " "),
                              "where": f"{folder}/{rel.as_posix()}", "kind": KINDS[f.suffix.lower()],
                              "updated": f.stat().st_mtime, "path": f})
        found.sort(key=lambda x: x["updated"], reverse=True)
        return found[:MAX_FILES]

    def open_file(self, fid: str) -> tuple[bytes, str]:
        for item in self.files():
            if item["id"] == fid:
                p: Path = item["path"]
                return p.read_bytes(), TYPES[p.suffix.lower()]
        raise BoardError("not on the board")

    def listing(self, errands: list[dict]) -> list[dict]:
        items = [{"id": p["id"], "title": p["title"], "kind": "link", "url": p["url"], "where": p.get("note", ""),
                  "updated": p["pinned"]} for p in self.pins()]
        items += [{"id": e["id"], "title": e.get("title", ""), "kind": "report", "where": f"errand · {e['kind']}",
                   "status": e.get("status"), "updated": e.get("finished") or e.get("approved", 0)}
                  for e in errands]
        items += [{k: v for k, v in f.items() if k != "path"} for f in self.files()]
        return items
