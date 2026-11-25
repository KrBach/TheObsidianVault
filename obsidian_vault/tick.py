"""Tally's tick: every few minutes the game asks Tally whether to go somewhere or say something.

It is one small, separate model call (no tools, no conversation history, a short sketch of
Tally instead of the whole core), so it costs little. The game sends where Tally and OWNER
are; this module turns that into a prompt grounded in the map, and the reply into a move.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime

from .config import REPO_ROOT
from .cat import ACTIONS as CAT_ACTIONS, Cat, CatError
from .moves import MoveLog

SKETCH_FILE = REPO_ROOT / "context" / "tally-tick.md"
NEAR = 3  # tiles around Tally that count as "near you"
BORING = {"wall", "floor", "floor_glint", "door", "desk_side", "graph_edge"}


def _room(world: dict, x: int, y: int) -> dict | None:
    for r in world.get("rooms", []):
        rx, ry, w, h = r["rect"]
        if rx <= x < rx + w and ry <= y < ry + h:
            return r
    return None


def _xy(v, default=(0, 0)) -> tuple[int, int]:
    try:
        return int(v["x"]), int(v["y"])
    except (KeyError, TypeError, ValueError):
        return default


def nearby(world: dict, x: int, y: int) -> list[str]:
    tiles, legend, grid = world.get("tiles", {}), world.get("legend", {}), world.get("map", [])
    seen: list[str] = []
    for yy in range(y - NEAR, y + NEAR + 1):
        for xx in range(x - NEAR, x + NEAR + 1):
            if 0 <= yy < len(grid) and 0 <= xx < len(grid[yy]):
                tid = legend.get(grid[yy][xx], "")
                name = tiles.get(tid, {}).get("name", tid).replace("Tally's", "your")
                if tid and tid not in BORING and name not in seen:
                    seen.append(name)
    return seen


def build_prompt(world: dict, payload: dict, memory: str = "", cat: str = "") -> str:
    tx, ty = _xy(payload.get("tally"), tuple(world.get("tally", (0, 0))))
    px, py = _xy(payload.get("player"), tuple(world.get("spawn", (0, 0))))
    room, proom = _room(world, tx, ty), _room(world, px, py)
    spots = world.get("spots", [])
    here = next((s for s in spots if tuple(s["at"]) == (tx, ty)), None)

    lines = [f"You are at ({tx},{ty}) in {room['name'] if room else 'the vault'}"
             + (f", {here['name']}" if here else "") + "."]
    if room:
        lines.append(f"The room: {room['desc']}")
    near = nearby(world, tx, ty)
    if near:
        lines.append("Near you: " + ", ".join(near) + ".")
    mins = payload.get("here_minutes")
    if isinstance(mins, (int, float)):
        lines.append(f"You have been here about {max(1, round(mins))} minute(s).")
    doing = " ".join(str(payload.get("doing") or "").split())[:200]
    why = " ".join(str(payload.get("why") or "").split())[:200]
    if doing:
        lines.append(f"What you are doing: {doing}.")
    if why:
        lines.append(f"Why: {why}")
    dist = max(abs(px - tx), abs(py - ty))
    lines.append(f"OWNER is at ({px},{py}) in {proom['name'] if proom else 'the vault'}, "
                 f"{dist} step(s) from you" + (" (same room)." if proom is room and room else "."))
    if cat:
        lines.append(cat + ' You may tend it with "cat": "pet", "feed", "play" or "call" (pet, feed and play only '
                     'when it is right next to you); usually leave it alone.')
    talk = payload.get("talk") if isinstance(payload.get("talk"), list) else []
    recent = " ".join(str(payload.get("recent") or "").split())[:300]
    reply = " ".join(str(payload.get("recent_reply") or "").split())[:300]
    if recent and not any(recent == t.get("said") for t in talk):
        talk = talk + [{"said": recent, "reply": reply}]
    if talk:
        lines.append("")
        lines.append("The end of your last conversation with OWNER (whatever you said you did there is done):")
        for t in talk[-2:]:
            if t.get("said"):
                lines.append(f'- OWNER: "{t["said"]}"')
            if t.get("reply"):
                lines.append(f'  You: "{t["reply"]}"')
    if memory:
        lines.append("")
        lines.append("Your own recent choices about where to be (oldest first):")
        lines.append(memory)
    thought = payload.get("thought")
    if isinstance(thought, str) and thought:
        lines.append("")
        lines.append(thought)
    lines.append("")
    lines.append("Places you can go (id: where, room):")
    rooms = {r["id"]: r["name"] for r in world.get("rooms", [])}
    for s in spots:
        lines.append(f"- {s['id']}: {s['name']}, {rooms.get(s['room'], s['room'])}")
    lines.append("")
    lines.append('Do you want to go somewhere, or stay ("stay" keeps what you are doing)? If you want a little round '
                 'after that, add up to two more places under "then", each with its own reason. Answer with the JSON object only.')
    return "\n".join(lines)


def parse(reply: str, world: dict) -> dict:
    """{"go": id or "stay", "at": [x, y] or None, "name": ..., "say": ...} from the model's reply."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    data: dict = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = {}
    go = str(data.get("go") or "stay").strip().lower()
    say = " ".join(str(data.get("say") or "").split())[:160]
    why = " ".join(str(data.get("why") or "").split())[:160]
    spots = {s["id"]: s for s in world.get("spots", [])}
    plan = []
    then = data.get("then") if isinstance(data.get("then"), list) else []
    for step in then[:2]:
        if isinstance(step, dict) and str(step.get("go", "")).strip().lower() in spots:
            sp = spots[str(step["go"]).strip().lower()]
            plan.append({"go": sp["id"], "at": sp["at"], "room": sp.get("room", ""), "name": sp["name"],
                         "why": " ".join(str(step.get("why") or "").split())[:160]})
    rest = str(data.get("rest") or "").strip().lower()
    rest = rest if rest in ("sit", "lie", "walk") else ""
    think = None
    if isinstance(data.get("think"), dict):
        about = " ".join(str(data["think"].get("about") or "").split())[:300]
        if about:
            think = {"about": about, "why": " ".join(str(data["think"].get("why") or "").split())[:300]}
    cat = str(data.get("cat") or "").strip().lower()
    spot = spots.get(go)
    if not spot:
        res = {"go": "stay", "at": None, "say": say, "why": why, "plan": plan, "rest": rest}
    else:
        res = {"go": spot["id"], "at": spot["at"], "room": spot.get("room", ""), "name": spot["name"], "say": say,
               "why": why, "plan": plan, "rest": rest}
    if think:
        res["think"] = think
    if cat in CAT_ACTIONS:
        res["tend_cat"] = cat
    return res


THOUGHT_BRIEF = """One of your short between-conversation check-ins (a small model with only a sketch of you)
flagged something it wanted to think harder about. You are the full model picking it up.

What to think about: {about}
Why it came up: {why}

Think it through. Read what you need in the vault; look things up on the web only if it truly helps.
Keep it small: a few minutes of work, not a project. Report in a few short paragraphs: what you
concluded, anything you would suggest to OWNER, and any question you would like to ask OWNER.
Nothing is written anywhere; the report is the result, and you will bring it up in the next conversation."""


class Thoughts:
    """Think-harder handoffs: a tick flags a thought, and a small read-only research errand takes it up.

    No approval is needed (OWNER's choice, 2026-11-25), so the limits are here: one open thought at a
    time, at most cfg.thoughts_per_day a day, read-only, and every one counted in thoughts.json.
    """

    def __init__(self, cfg, errands):
        self.cfg, self.errands = cfg, errands
        self.file = cfg.state_dir / "thoughts.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def open(self) -> dict | None:
        """The thought still being worked on, if any."""
        for t in reversed(self._load().get("log", [])):
            rec = self.errands.get(t.get("errand", "")) if self.errands else None
            if rec and rec.get("status") == "running":
                return t
        return None

    def note_for_tick(self) -> str:
        """A line for the tick prompt: whether a thought can be handed off now."""
        if not self.available():
            t = self.open()
            if t:
                return f'You are already thinking harder about "{t["about"]}"; the answer comes in the next conversation.'
            return ""
        return ('If something deserves more thought than a check-in can give (something from the conversation, the '
                'vault, your own work), you may add "think": {"about": "...", "why": "..."} and a fuller version of '
                'you will think it through, read-only, before the next conversation. At most one; usually leave it out.')

    def available(self) -> bool:
        cfg, per_day = self.cfg, int(getattr(self.cfg, "thoughts_per_day", 0) or 0)
        if per_day <= 0 or not self.errands or not getattr(cfg, "errands", False) or not getattr(cfg, "claude_path", ""):
            return False
        if getattr(cfg, "writes_mode", "off") == "off" or self.open():
            return False
        return self._load().get("days", {}).get(self._today(), 0) < per_day

    def start(self, think: dict) -> str | None:
        """Start the errand for a flagged thought. Returns its id, or None when the limits say no."""
        if not self.available():
            return None
        eid = f"t{int(time.time() * 1000)}"
        brief = getattr(self.cfg, "fill", lambda t: t)(THOUGHT_BRIEF.format(about=think["about"], why=think.get("why") or "(no reason given)"))
        try:
            self.errands.start({"id": eid, "errand_kind": "research", "title": f"Thinking harder: {think['about'][:80]}",
                                "brief": brief, "self_started": True})
        except Exception as e:  # noqa: BLE001 - a thought that cannot start is simply dropped
            print(f"  thought not started: {e}")
            return None
        data = self._load()
        days = data.setdefault("days", {})
        days[self._today()] = days.get(self._today(), 0) + 1
        data["total"] = int(data.get("total", 0)) + 1
        data.setdefault("log", []).append({"errand": eid, "about": think["about"], "why": think.get("why", ""),
                                           "at": time.time()})
        data["log"] = data["log"][-50:]
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)
        return eid


class LastTalk:
    """The end of the last conversation (OWNER's line and Tally's answer), kept so a tick can build on it
    even after the page was reloaded."""

    def __init__(self, state_dir):
        self.file = state_dir / "last-talk.json"

    def get(self) -> list[dict]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def add(self, said: str, reply: str) -> None:
        said = " ".join(said.split())[:300]
        reply = " ".join(reply.split())
        reply = reply if len(reply) <= 300 else reply[:297] + "..."
        if not said:
            return
        talk = (self.get() + [{"said": said, "reply": reply}])[-2:]
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(talk, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class Ticker:
    def __init__(self, cfg, brain, errands=None):
        self.cfg, self.brain = cfg, brain
        self.lock = threading.Lock()
        self.thoughts = Thoughts(cfg, errands)
        self.last_talk = LastTalk(cfg.state_dir)
        self.cat = Cat(cfg.state_dir)
        self.moves = MoveLog(cfg.state_dir, getattr(cfg, "timezone", ""), getattr(cfg, "fill", None))
        try:
            self.sketch = SKETCH_FILE.read_text(encoding="utf-8")
        except OSError:
            self.sketch = "You are Tally. Answer with one JSON object: {\"go\": \"<place id or stay>\", \"say\": \"\"}"

    def tick(self, world: dict, payload: dict) -> dict:
        # Never while Tally is answering the owner, and never two ticks at once.
        brain_lock = getattr(self.brain, "lock", None)
        if (brain_lock is not None and brain_lock.locked()) or not self.lock.acquire(blocking=False):
            return {"go": "stay", "at": None, "say": "", "skipped": "busy"}
        try:
            payload = {**payload, "talk": self.last_talk.get(), "thought": self.thoughts.note_for_tick()}
            if isinstance(payload.get("cat"), dict):
                try:
                    self.cat.save(payload["cat"])
                except CatError:
                    pass
            prompt = build_prompt(world, payload, self.moves.for_prompt(4), self.cat.for_prompt(payload.get("tally")))
            fill = getattr(self.cfg, "fill", lambda t: t)
            reply = self.brain.muse(fill(self.sketch), fill(prompt), self.cfg.tick_model)
        except Exception as e:  # a failed tick just means Tally stays put
            return {"go": "stay", "at": None, "say": "", "error": f"{type(e).__name__}: {e}"[:300]}
        finally:
            self.lock.release()
        res = parse(reply, world)
        think = res.pop("think", None)
        action = res.pop("tend_cat", None)
        if action:  # the same rules as the tool: next to it, and the cat decides how it takes it
            try:
                done = self.cat.tend("tally", action, payload.get("tally"))
            except CatError:
                done = None
            if done and done.get("ok"):
                res["cat"] = done
        if think and self.thoughts.start(think):
            res["thinking"] = think["about"]
        # Write the choice down in Tally's own words, so the conversation and the next tick remember it.
        kind = "settle" if res.get("rest") else ("stay" if res["go"] == "stay" else "go")
        self.moves.add("tick", kind, res.get("name", "") if res["go"] != "stay" else "", res.get("why", ""),
                       res.get("say", ""), res.get("plan"), res.get("room", ""), how=res.get("rest", ""))
        return res
