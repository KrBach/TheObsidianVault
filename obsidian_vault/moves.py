"""Tally's own record of where they chose to go, and why.

Tally's moves come from two places: the game tools in a conversation (go_to, stay_here, ...) and
the tick, a separate small call every few minutes. Neither remembers the other. This journal is
the shared memory: every choice is written down in Tally's own words when it is made, and the
last few are handed back to both the conversation and the next tick, as Tally's own decisions.

Kept in <state_dir>/moves.jsonl, newest last, trimmed to the last MAX entries.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

MAX = 200
_lock = threading.Lock()


def _clean(text, n=200) -> str:
    return " ".join(str(text or "").split())[:n]


def _ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    return f"{h} h {m % 60} min ago" if m % 60 else f"{h} h ago"


class MoveLog:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "moves.jsonl"

    def add(self, source: str, kind: str, place: str = "", why: str = "", say: str = "",
            plan: list[dict] | None = None, room: str = "") -> dict:
        rec = {"time": time.time(), "source": source, "kind": kind, "place": _clean(place, 120),
               "room": _clean(room, 60), "why": _clean(why), "say": _clean(say, 160)}
        if plan:
            rec["plan"] = [{"place": _clean(p.get("name") or p.get("place"), 120), "why": _clean(p.get("why"))}
                           for p in plan][:3]
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
            lines.append(json.dumps(rec, ensure_ascii=False))
            self.path.write_text("\n".join(lines[-MAX:]) + "\n", encoding="utf-8")
        return rec

    def recent(self, n: int = 6, within_hours: float = 12) -> list[dict]:
        if not self.path.exists():
            return []
        cutoff = time.time() - within_hours * 3600
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("time", 0) >= cutoff:
                out.append(rec)
        return out[-n:]

    @staticmethod
    def line(rec: dict, now: float | None = None) -> str:
        now = now or time.time()
        try:
            tz = ZoneInfo("Europe/Copenhagen") if ZoneInfo else None
            clock = datetime.fromtimestamp(rec["time"], tz).strftime("%H:%M")
        except Exception:  # no tz database on this machine
            clock = datetime.fromtimestamp(rec["time"]).strftime("%H:%M")
        where = rec.get("place") or "where you were"
        how = "in a conversation with Kristian" if rec.get("source") == "talk" else "on your own, between conversations"
        kind = rec.get("kind")
        if kind == "go":
            what = f"you chose to go to {where}"
        elif kind == "stay":
            what = f"you chose to stay {where}" if rec.get("place") else "you chose to stay where you were"
        elif kind == "follow":
            what = "you chose to walk with Kristian"
        elif kind == "free":
            what = "you let go of your last plan, to choose again on your own"
        else:
            what = f"you chose: {kind}"
        text = f"{clock} ({_ago(now - rec['time'])}), {how}: {what}"
        if rec.get("why"):
            text += f'. Your reason: "{rec["why"]}"'
        if rec.get("say"):
            text += f'. You said: "{rec["say"]}"'
        if rec.get("plan"):
            text += ". Then you meant to go on to " + "; then ".join(
                f"{p['place']}" + (f' ("{p["why"]}")' if p.get("why") else "") for p in rec["plan"])
        return text + "."

    def for_prompt(self, n: int = 6) -> str:
        recs = self.recent(n)
        if not recs:
            return ""
        now = time.time()
        return "\n".join(f"- {self.line(r, now)}" for r in recs)
