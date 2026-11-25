"""Tally's tick: every few minutes the game asks Tally whether to go somewhere or say something.

It is one small, separate model call (no tools, no conversation history, a short sketch of
Tally instead of the whole core), so it costs little. The game sends where Tally and Kristian
are; this module turns that into a prompt grounded in the map, and the reply into a move.
"""

from __future__ import annotations

import json
import re
import threading

from .config import REPO_ROOT
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


def build_prompt(world: dict, payload: dict, memory: str = "") -> str:
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
    lines.append(f"Kristian is at ({px},{py}) in {proom['name'] if proom else 'the vault'}, "
                 f"{dist} step(s) from you" + (" (same room)." if proom is room and room else "."))
    recent = " ".join(str(payload.get("recent") or "").split())[:300]
    reply = " ".join(str(payload.get("recent_reply") or "").split())[:300]
    if recent:
        lines.append(f'The last thing he said to you: "{recent}"')
    if reply:
        lines.append(f'What you answered (whatever you said you did there is done): "{reply}"')
    if memory:
        lines.append("")
        lines.append("Your own recent choices about where to be (oldest first):")
        lines.append(memory)
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
    spot = spots.get(go)
    if not spot:
        return {"go": "stay", "at": None, "say": say, "why": why, "plan": plan}
    return {"go": spot["id"], "at": spot["at"], "room": spot.get("room", ""), "name": spot["name"], "say": say,
            "why": why, "plan": plan}


class Ticker:
    def __init__(self, cfg, brain):
        self.cfg, self.brain = cfg, brain
        self.lock = threading.Lock()
        self.moves = MoveLog(cfg.state_dir)
        try:
            self.sketch = SKETCH_FILE.read_text(encoding="utf-8")
        except OSError:
            self.sketch = "You are Tally. Answer with one JSON object: {\"go\": \"<place id or stay>\", \"say\": \"\"}"

    def tick(self, world: dict, payload: dict) -> dict:
        # Never while Tally is answering Kristian, and never two ticks at once.
        brain_lock = getattr(self.brain, "lock", None)
        if (brain_lock is not None and brain_lock.locked()) or not self.lock.acquire(blocking=False):
            return {"go": "stay", "at": None, "say": "", "skipped": "busy"}
        try:
            prompt = build_prompt(world, payload, self.moves.for_prompt(4))
            reply = self.brain.muse(self.sketch, prompt, self.cfg.tick_model)
        except Exception as e:  # a failed tick just means Tally stays put
            return {"go": "stay", "at": None, "say": "", "error": f"{type(e).__name__}: {e}"[:300]}
        finally:
            self.lock.release()
        res = parse(reply, world)
        # Write the choice down in Tally's own words, so the conversation and the next tick remember it.
        if res["go"] == "stay":
            self.moves.add("tick", "stay", "", res.get("why", ""), res.get("say", ""), res.get("plan"))
        else:
            self.moves.add("tick", "go", res["name"], res.get("why", ""), res.get("say", ""), res.get("plan"),
                           res.get("room", ""))
        return res
