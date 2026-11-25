"""The vault cat: the rules for tending it, and where it is, kept across sessions.

The cat walks about in the browser (scripted, no model calls). The browser tells the server where
it is and what mood it is in every few seconds; this module keeps that in <state_dir>/cat.json
together with when it was last fed, so the next session finds it where it was and as hungry as
it should be.

Tending the cat (pet, feed, play, call) goes through here for everyone, so the rules are the same
for OWNER, a guest and Tally: OWNER clicks, Tally uses the tend_cat tool or a tick. The answer is a
one-line emote for the talk box and an effect the browser plays out. Tally's own tending is queued
here and handed to the browser when the reply ends.

The cat's name and look come from world/tiles.json (creatures.cat), not from code.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from .config import REPO_ROOT

HUNGRY_AFTER = 3 * 3600  # seconds after a meal until the cat is hungry again
MOODS = ("sleepy", "curious", "hungry", "playful")
ACTIONS = ("pet", "feed", "play", "call")
GUEST_ACTIONS = ("pet", "call")
CALL_REACH = 8  # steps: calling works across a room, everything else needs you next to the cat
FOLLOW_SECONDS = 60


class CatError(Exception):
    pass


def cat_name() -> str:
    try:
        data = json.loads((REPO_ROOT / "world" / "tiles.json").read_text(encoding="utf-8"))
        return str(data["creatures"]["cat"].get("call") or data["creatures"]["cat"]["name"])
    except (OSError, ValueError, KeyError, TypeError):
        return "the cat"


def _cell(v) -> tuple[int, int] | None:
    try:
        x, y = (v["x"], v["y"]) if isinstance(v, dict) else v
        return int(x), int(y)
    except (KeyError, TypeError, ValueError):
        return None


class Cat:
    def __init__(self, state_dir: Path, rng=random.random, clock=time.time):
        self.file = Path(state_dir) / "cat.json"
        self.rng, self.clock = rng, clock
        self.name = cat_name()

    # ---------- state ----------

    def _load(self) -> dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(f".{self.file.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)

    def get(self) -> dict:
        """Where the cat is and how it is, as last saved, with hunger worked out from the clock."""
        data = self._load()
        now = self.clock()
        fed = float(data.get("fed_at") or 0)
        if not fed:  # never fed in this vault: count from the first time it was seen
            fed = float(data.get("first_seen") or now)
        hungry = now - fed >= HUNGRY_AFTER
        mood = data.get("mood") if data.get("mood") in MOODS else "sleepy"
        if hungry:
            mood = "hungry"
        elif mood == "hungry":
            mood = "sleepy"
        out = {"x": data.get("x"), "y": data.get("y"), "mood": mood, "asleep": bool(data.get("asleep")),
               "on": str(data.get("on") or "")[:40], "fed_at": fed, "hungry": hungry,
               "hungry_in_minutes": 0 if hungry else round((fed + HUNGRY_AFTER - now) / 60), "name": self.name}
        if _cell(out) is None:
            out["x"] = out["y"] = None
        return out

    def save(self, rec: dict) -> dict:
        """The browser's report: where the cat is, its mood, whether it sleeps."""
        if not isinstance(rec, dict):
            raise CatError("the cat's state must be an object")
        cell = _cell(rec)
        if cell is None:
            raise CatError("the cat's state needs x and y")
        data = self._load()
        data.setdefault("first_seen", self.clock())
        data["x"], data["y"] = cell
        mood = str(rec.get("mood") or "")
        if mood in MOODS:
            data["mood"] = mood
        data["asleep"] = bool(rec.get("asleep"))
        data["on"] = " ".join(str(rec.get("on") or "").split())[:40]
        data["saved_at"] = self.clock()
        self._write(data)
        return self.get()

    # ---------- tending ----------

    def tend(self, who: str, action: str, at, cat=None, queue: bool = False) -> dict:
        """One act of tending. who: owner, guest or tally. at: where the one tending stands.
        cat: where the browser has the cat right now (it walks; the saved cell can be a step behind).
        Returns {"ok", "text", "effect"}; effect is what the browser plays out."""
        action = str(action or "").strip().lower()
        if action not in ACTIONS:
            raise CatError(f"no such way to tend the cat: {action!r} (pet, feed, play or call)")
        if who == "guest" and action not in GUEST_ACTIONS:
            raise CatError(f"a guest may pet or call {self.name}, not {action} it")
        if cat is not None and _cell(cat) is not None and who != "guest":
            self.save({**self._load(), **(cat if isinstance(cat, dict) else {"x": cat[0], "y": cat[1]})})
        state = self.get()
        if cat is not None and _cell(cat) is not None and who == "guest":  # a guest's visit is not saved
            state = {**state, **{k: v for k, v in (cat.items() if isinstance(cat, dict) else []) if k in ("x", "y", "asleep", "mood")}}
            state["x"], state["y"] = _cell(cat)
        me, there = _cell(at), _cell(state)
        if me is None or there is None:
            raise CatError(f"nobody knows where {self.name} is just now")
        dist = max(abs(me[0] - there[0]), abs(me[1] - there[1]))
        n = self.name
        if action == "call":
            if dist > CALL_REACH:
                return {"ok": False, "text": f"{n} is too far off to hear you.", "effect": "none"}
        elif dist > 1:
            return {"ok": False, "text": f"You need to be next to {n} for that.", "effect": "none"}

        asleep, mood = state["asleep"], state["mood"]
        if action == "pet":
            if asleep:
                res = {"text": f"{n} opens one eye, blinks slowly at you, and goes back to sleep.", "effect": "blink"}
            elif self.rng() < 0.25:
                res = {"text": f"{n} purrs for a moment, then stands up mid-stroke and walks off, as cats do.", "effect": "walk_off"}
            else:
                res = {"text": f"{n} purrs.", "effect": "purr"}
        elif action == "feed":
            data = self._load()
            data["fed_at"] = self.clock()
            data["mood"], data["asleep"] = "playful", False
            data.setdefault("first_seen", data["fed_at"])
            self._write(data)
            res = {"text": f"The bowl by the fireplace is filled. {n} eats, then follows you about for a while.",
                   "effect": "fed", "follow_seconds": FOLLOW_SECONDS}
        elif action == "play":
            if asleep:
                res = {"text": f"{n} is asleep and ignores the quill.", "effect": "ignore"}
            elif mood == "playful":
                res = {"text": f"{n} crouches, wiggles, and pounces on the quill.", "effect": "pounce"}
            else:
                res = {"text": f"{n} ignores the quill.", "effect": "ignore"}
        else:  # call
            if asleep:
                res = {"text": f"{n}'s ear twitches. It stays asleep.", "effect": "none"}
            else:
                res = {"text": f"{n} heard you, and comes over slowly, as if it had been going that way anyway.", "effect": "come"}
        res = {"ok": True, "action": action, "who": who, "at": list(me), **res}
        if queue:
            data = self._load()
            data["pending"] = (data.get("pending") or [])[-4:] + [res]
            self._write(data)
        return res

    def take_pending(self) -> list[dict]:
        """Tally's tending since the last reply, for the browser to play out."""
        data = self._load()
        pending = data.pop("pending", None) or []
        if pending:
            self._write(data)
        return pending

    # ---------- for Tally ----------

    def for_prompt(self, tally_at=None) -> str:
        s = self.get()
        if s["x"] is None:
            return ""
        what = "asleep" if s["asleep"] else {"sleepy": "drowsy", "curious": "curious, looking about",
                                               "hungry": "hungry", "playful": "playful"}[s["mood"]]
        line = f"{s['name']}, the vault cat, is at ({s['x']},{s['y']})" + (f", {s['on']}" if s["on"] else "") + f": {what}."
        if s["hungry"]:
            line += " It has not been fed for over three hours."
        me = _cell(tally_at)
        if me:
            d = max(abs(me[0] - s["x"]), abs(me[1] - s["y"]))
            line += " It is right next to you." if d <= 1 else f" It is {d} steps from you."
        return line
