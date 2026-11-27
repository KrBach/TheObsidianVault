"""The vault cat: the rules for tending it, and where it is, kept across sessions.

The cat walks about in the browser (scripted, no model calls). The browser tells the server where
it is and what mood it is in every few seconds; this module keeps that in <state_dir>/cat.json
together with when it was last fed, so the next session finds it where it was and as hungry as
it should be.

Tending the cat (pet, feed, play, call) goes through here for everyone, so the rules are the same
for OWNER, a guest and Tally: OWNER clicks, Tally uses the tend_cat tool or a tick. The answer is a
one-line emote for the talk box and an effect the browser plays out. Tally's own tending is queued
here and handed to the browser when the reply ends.

The cat also remembers how much it likes Tally and OWNER (affinity, kept in cat.json and fading
slowly over days). Its odds for walking off from a pet, and for staying put when someone it likes
walks towards it, are worked out here; the browser uses them and the affinity to weigh where it goes.
Tally only ever reads it in words.

The cat's name and look come from world/tiles.json (creatures.cat), not from code.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from . import atomic
from .config import REPO_ROOT

HUNGRY_AFTER = 3 * 3600  # seconds after a meal until the cat is hungry again
MOODS = ("sleepy", "curious", "hungry", "playful")
ACTIONS = ("pet", "feed", "play", "call")
GUEST_ACTIONS = ("pet", "call")
CALL_REACH = 8  # steps: calling works across a room, everything else needs you next to the cat
FOLLOW_SECONDS = 60

# Affinity: how much the cat likes Tally and OWNER, 0 to 1. Tending that lands raises it (a meal most,
# a game next, an accepted pet a little; a pet it walks off from adds nothing), and it fades over days
# towards a floor, never to nothing. It only shifts the cat's odds; it never makes the cat obedient.
FOND_OF = ("tally", "owner")  # guests are not remembered
AFFINITY_FLOOR = 0.15
AFFINITY_HALF_LIFE = 3 * 86400  # seconds for the part above the floor to halve
AFFINITY_GAIN = {"fed": 0.15, "pounce": 0.08, "purr": 0.03}  # by the effect that actually happened
WALK_OFF = 0.25  # odds a pet ends with it walking off, for someone it barely knows
FONDNESS = ((0.3, "keeps a polite distance from"), (0.5, "is getting used to"),
            (0.75, "seems fond of"), (1.01, "is very fond of"))

# Laps. When Tally or OWNER sits (on a chair, couch, bench, cushion or the floor) and the cat is sleepy,
# it may walk over and fall asleep on that lap: mostly if it is fond of them, rarely if friendly, never
# if wary (the parser's tiers). On a lap it shares the sitter's cell, the one time it shares anyone's.
# If the sitter gets up or moves, it hops down beside them, offended; that costs no affinity.
TIERS = {"friendly_from": 0.3, "fond_from": 0.75}  # fallback; world/vellum-parser.json "tiers" wins
LAP_ODDS = {"fond": 0.6, "friendly": 0.06, "wary": 0.0}  # each time it picks somewhere to sleep
LAP_POSES = ("sit",)


def fondness(value: float) -> str:
    """How the cat is with someone, in words (the numbers stay out of what Tally reads)."""
    for limit, words in FONDNESS:
        if value < limit:
            return words
    return FONDNESS[-1][1]


def stay_odds(value: float) -> float:
    """Odds the cat stays put when someone it likes is walking towards it, instead of wandering off.
    Even at the most it sometimes goes anyway: it is a cat."""
    return round(max(0.0, min(0.7, (value - AFFINITY_FLOOR) / (1 - AFFINITY_FLOOR) * 0.7)), 3)


def walk_off_odds(value: float) -> float:
    """Odds a pet ends with the cat walking off: lower for someone it likes, never zero."""
    return round(WALK_OFF * (1 - 0.6 * max(0.0, min(1.0, value))), 3)


def tiers() -> dict:
    """The affinity steps for wary, friendly and fond, from world/vellum-parser.json (one place for them)."""
    try:
        t = json.loads((REPO_ROOT / "world" / "vellum-parser.json").read_text(encoding="utf-8")).get("tiers", {})
        return {k: float(t.get(k, v)) for k, v in TIERS.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(TIERS)


def tier(value: float, steps_: dict | None = None) -> str:
    """wary, friendly or fond, for an affinity."""
    t = {**TIERS, **(steps_ or tiers())}
    if value >= float(t["fond_from"]):
        return "fond"
    if value >= float(t["friendly_from"]):
        return "friendly"
    return "wary"


def lap_odds(value: float, steps_: dict | None = None) -> float:
    """Odds the sleepy cat picks this sitter's lap when it looks for somewhere to sleep."""
    return LAP_ODDS[tier(value, steps_)]


def lap_ok(mood: str, pose) -> bool:
    """Only a sleepy cat goes to a lap, and only to someone sitting (pose "sit", or {"how": "sit"})."""
    how = pose.get("how") if isinstance(pose, dict) else pose
    return mood == "sleepy" and how in LAP_POSES


def stays_on_lap(cat, sitter) -> bool:
    """Still on the lap: the sitter is on the cat's cell and still sitting. Otherwise it hops down."""
    if not isinstance(sitter, dict):
        return False
    c, s = _cell(cat), _cell(sitter)
    return c is not None and c == s and lap_ok("sleepy", sitter.get("pose"))


def lap_text(who: str, name: str) -> str:
    """The cat on a lap, for Tally's game_state: "your" lap is Tally's."""
    return f"{name} is asleep on your lap." if who == "tally" else f"{name} is asleep on OWNER's lap."


def hop_down_text(name: str) -> str:
    return f"{name} hops down, offended."


def steps(a, b) -> int | None:
    """Steps between two cells, diagonals counting as one: the one distance rule for the cat. What
    Tally is told ("right next to you"), tend_cat and the text parser all ask this, so they cannot
    disagree. None if either cell is unknown."""
    p, q = _cell(a), _cell(b)
    if p is None or q is None:
        return None
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]))


def next_to(a, b) -> bool:
    """Right next to (or on the same cell as) each other."""
    d = steps(a, b)
    return d is not None and d <= 1


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
        atomic.write_text(self.file, json.dumps(data, ensure_ascii=False))

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
        lap = data.get("lap") if data.get("lap") in FOND_OF and data.get("asleep") else ""
        out = {"x": data.get("x"), "y": data.get("y"), "mood": mood, "asleep": bool(data.get("asleep")),
               "on": str(data.get("on") or "")[:40], "lap": lap, "fed_at": fed, "hungry": hungry,
               "hungry_in_minutes": 0 if hungry else round((fed + HUNGRY_AFTER - now) / 60), "name": self.name}
        if _cell(out) is None:
            out["x"] = out["y"] = None
        aff = {who: self.affinity(who, data) for who in FOND_OF}
        out["affinity"] = aff  # for the browser's odds; Tally reads the words
        out["stay_odds"] = {who: stay_odds(v) for who, v in aff.items()}
        steps_ = tiers()
        out["lap_odds"] = {who: lap_odds(v, steps_) for who, v in aff.items()}
        out["hop_down_text"] = hop_down_text(self.name)
        out["fondness"] = {who: fondness(v) for who, v in aff.items()}
        out["fondness_text"] = (f"{self.name} {out['fondness']['tally']} you, and {out['fondness']['owner']} OWNER.")
        return out

    # ---------- affinity ----------

    def affinity(self, who: str, data: dict | None = None) -> float:
        """How much the cat likes who (tally or owner) now, faded from when it was last raised."""
        all_ = (data if data is not None else self._load()).get("affinity")
        rec = all_.get(who) if isinstance(all_, dict) else None
        try:
            value, at = float(rec["value"]), float(rec["at"])
        except (KeyError, TypeError, ValueError):
            return AFFINITY_FLOOR
        value = max(AFFINITY_FLOOR, min(1.0, value))
        days = max(0.0, self.clock() - at) / AFFINITY_HALF_LIFE
        return round(AFFINITY_FLOOR + (value - AFFINITY_FLOOR) * 0.5 ** days, 4)

    def _raise(self, who: str, effect: str) -> None:
        gain = AFFINITY_GAIN.get(effect, 0)
        if who not in FOND_OF or not gain:
            return
        data = self._load()
        now = self.affinity(who, data)
        if not isinstance(data.get("affinity"), dict):
            data["affinity"] = {}
        data["affinity"][who] = {"value": round(now + gain * (1 - now), 4), "at": self.clock()}
        self._write(data)

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
        # on a lap: whose (the browser keeps it there only while they sit); asleep, or it is not a lap
        data["lap"] = rec.get("lap") if rec.get("lap") in FOND_OF and data["asleep"] else ""
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
        dist = steps(me, there)
        n = self.name
        if who == "tally" and dist > 1 and self._next_at_talk():
            # Tally was told, when OWNER's message came in, that the cat was right next to them. Positions saved
            # since can be a step off (the cat shifts, or a save lags), so that word counts: same rule, same moment.
            dist = 1
        if action == "call":
            if dist > CALL_REACH:
                return {"ok": False, "text": f"{n} is too far off to hear you.", "effect": "none"}
        elif dist > 1:
            return {"ok": False, "text": f"You need to be next to {n} for that (you are at ({me[0]},{me[1]}), {n} at ({there[0]},{there[1]})).",
                    "effect": "none"}

        asleep, mood = state["asleep"], state["mood"]
        if action == "pet":
            if asleep:
                res = {"text": f"{n} opens one eye, blinks slowly at you, and goes back to sleep.", "effect": "blink"}
            elif self.rng() < (walk_off_odds(self.affinity(who)) if who in FOND_OF else WALK_OFF):
                res = {"text": f"{n} purrs for a moment, then stands up mid-stroke and walks off, as cats do.", "effect": "walk_off"}
            else:
                res = {"text": f"{n} purrs.", "effect": "purr"}
        elif action == "feed":
            data = self._load()
            data["fed_at"] = self.clock()
            data["mood"], data["asleep"], data["lap"] = "playful", False, ""
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
        self._raise(who, res["effect"])  # only tending that landed counts; a guest's is not remembered
        now = self.get()
        res = {"ok": True, "action": action, "who": who, "at": list(me), **res,
               **{k: now[k] for k in ("affinity", "stay_odds", "fondness", "fondness_text")}}
        if queue:
            data = self._load()
            data["pending"] = (data.get("pending") or [])[-4:] + [res]
            self._write(data)
        return res

    def note_talk(self, cat, tally) -> None:
        """Where the cat and Tally were when OWNER's message was sent: what <game_state> told Tally."""
        c, t = _cell(cat), _cell(tally)
        if c is None or t is None:
            return
        try:
            self.save(cat)  # the freshest position there is
        except CatError:
            pass
        data = self._load()
        data["at_talk"] = {"cat": list(c), "tally": list(t), "time": self.clock()}
        self._write(data)

    def _next_at_talk(self, within: float = 600) -> bool:
        seen = self._load().get("at_talk") or {}
        if self.clock() - float(seen.get("time") or 0) > within:
            return False
        return next_to(seen.get("cat"), seen.get("tally"))

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
        if s["lap"]:
            base = lap_text(s["lap"], s["name"])[:-1] + f", at ({s['x']},{s['y']})"
            if s["lap"] == "tally":
                return f"{base}. You can tend it with tend_cat. {s['fondness_text']}"
            d = steps(tally_at, s)
            near = "" if d is None else ", right next to you" if d <= 1 else f", {d} steps from you"
            return f"{base}{near}. {s['fondness_text']}"
        line = f"{s['name']}, the vault cat, is at ({s['x']},{s['y']})" + (f", {s['on']}" if s["on"] else "") + f": {what}."
        if s["hungry"]:
            line += " It has not been fed for over three hours."
        d = steps(tally_at, s)
        if d is not None:
            line += " It is right next to you." if d <= 1 else f" It is {d} steps from you."
        return line + " " + s["fondness_text"]
