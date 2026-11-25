"""The vault's layout, plus the changes Tally has made to it.

world/vault.json and world/tiles.json in the repo are the vault as built. Tally may move
furniture and name new places; those changes live in <state_dir>/world.json (git-ignored,
so a pull never overwrites them) and are laid over the map whenever the game loads it.

Every change is checked with the same map check as the repo (check_map.check): if a candle
would block a doorway or a pool would cut a room off, the change is refused with the reason.
Walls, doors, shelves and Tally's desk cannot be moved from here.

Tally also tells the game where they mean to be (go somewhere, stay put, follow OWNER, or
wander freely). That intent is written to <state_dir>/intent.json by the vault tools and
picked up by the game when Tally's reply ends.

Where Tally is right now (their cell, what they are doing and why, their pose, a plan) is saved
by the game to <state_dir>/tally.json, so the next time the game opens Tally is where they were.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .check_map import check
from .config import REPO_ROOT

WORLD_FILE = REPO_ROOT / "world" / "vault.json"
TILES_FILE = REPO_ROOT / "world" / "tiles.json"

# Furniture Tally may place or clear. Everything else (walls, doors, shelves, the desk) is fixed.
MOVABLE = ["candle", "brazier", "papers", "mat", "pool", "scribe_desk", "map_table", "graph_node", "graph_edge",
           "chair", "chair_ne", "bench", "couch", "table", "hammock", "bed", "treadmill", "cushion"]
FLOORS = {"floor", "floor_glint"}
POSES = ("sit", "lie", "walk")
MAX_CHANGES = 400
MAX_SPOTS = 30


class WorldError(Exception):
    """A change to the vault that is not allowed. The message is shown to Tally."""


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class World:
    def __init__(self, state_dir: Path):
        self.state = Path(state_dir)
        self.file = self.state / "world.json"
        self.intent_file = self.state / "intent.json"
        self.tally_file = self.state / "tally.json"

    # ---------- loading ----------

    def overlay(self) -> dict:
        data = _read_json(self.file, {})
        return {"tiles": dict(data.get("tiles", {})), "spots": list(data.get("spots", []))}

    def base(self) -> dict:
        world = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
        world.update(json.loads(TILES_FILE.read_text(encoding="utf-8")))
        return world

    def load(self, overlay: dict | None = None) -> dict:
        world = self.base()
        ov = self.overlay() if overlay is None else overlay
        rows = [list(r) for r in world["map"]]
        moved = set()  # cells where the furniture of the built map was cleared or replaced
        for key, ch in ov["tiles"].items():
            x, y = (int(v) for v in key.split(","))
            if 0 <= y < len(rows) and 0 <= x < len(rows[y]) and ch in world["legend"]:
                base_id = world["legend"].get(rows[y][x], "")
                if base_id not in FLOORS and base_id not in MOVABLE:
                    continue  # the built map has something fixed there now (a wall, the fireplace): it wins
                was = world["tiles"].get(base_id, {})
                if was.get("use") and ch != rows[y][x]:
                    moved.add((x, y))
                rows[y][x] = ch
        world["map"] = ["".join(r) for r in rows]
        # A built-in place on a piece of furniture that has since moved points at nothing: drop it.
        built = [s for s in world.get("spots", []) if tuple(s["at"]) not in moved]
        base_ids = {s["id"] for s in built}
        world["spots"] = built + [
            {**s, "mine": True} for s in ov["spots"] if s.get("id") not in base_ids]
        return world

    def version(self) -> float:
        try:
            return self.file.stat().st_mtime
        except OSError:
            return 0.0

    # ---------- helpers ----------

    def _char_for(self, world: dict, item: str) -> str:
        for ch, tid in world["legend"].items():
            if tid == item and ch not in ("T",):
                return ch
        raise WorldError(f"no map character for {item}")

    def _tile(self, world: dict, x: int, y: int) -> str:
        grid = world["map"]
        if not (0 <= y < len(grid) and 0 <= x < len(grid[y])):
            raise WorldError(f"({x},{y}) is outside the vault")
        return world["legend"].get(grid[y][x], "")

    def room_at(self, world: dict, x: int, y: int) -> dict | None:
        for r in world.get("rooms", []):
            rx, ry, w, h = r["rect"]
            if rx <= x < rx + w and ry <= y < ry + h:
                return r
        return None

    def _protected(self, world: dict, x: int, y: int) -> str:
        if [x, y] in (world.get("spawn"), world.get("exit"), world.get("tally")):
            return "the way in and Tally's desk stay clear"
        for s in world.get("spots", []):
            if s["at"] == [x, y]:
                return f"that is the place '{s['id']}'; something solid there would leave nowhere to stand"
        return ""

    def _commit(self, ov: dict, what: str) -> dict:
        world = self.load(ov)
        problems = check(world, world["tiles"])
        if problems:
            raise WorldError(f"{what} would break the map: " + "; ".join(problems[:3]))
        _write_json(self.file, ov)
        with open(self.state / "world-journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "change": what}, ensure_ascii=False) + "\n")
        return world

    # ---------- changes ----------

    def place(self, item: str, x: int, y: int) -> str:
        item = str(item).strip().lower().replace(" ", "_")
        if item not in MOVABLE:
            raise WorldError(f"{item} cannot be placed; you can place: {', '.join(MOVABLE)}")
        x, y = int(x), int(y)
        world = self.load()
        here = self._tile(world, x, y)
        if here not in FLOORS and here not in MOVABLE:
            raise WorldError(f"({x},{y}) is {here or 'nothing'}; furniture goes on the floor")
        solid = world["tiles"][item].get("solid")
        why = self._protected(world, x, y) if solid else ""
        if why:
            raise WorldError(why)
        ov = self.overlay()
        if len(ov["tiles"]) >= MAX_CHANGES:
            raise WorldError("too many changes; clear something first")
        ov["tiles"][f"{x},{y}"] = self._char_for(world, item)
        room = self.room_at(world, x, y)
        self._commit(ov, f"place {item} at ({x},{y})")
        return f"Placed a {world['tiles'][item]['name']} at ({x},{y}) in {room['name'] if room else 'the vault'}."

    def clear(self, x: int, y: int) -> str:
        x, y = int(x), int(y)
        world = self.load()
        here = self._tile(world, x, y)
        if here not in MOVABLE:
            raise WorldError(f"({x},{y}) is {here or 'nothing'}, which cannot be moved from here")
        ov = self.overlay()
        ov["tiles"][f"{x},{y}"] = "."
        self._commit(ov, f"clear ({x},{y})")
        return f"Cleared the {world['tiles'][here]['name']} at ({x},{y})."

    def name_place(self, pid: str, name: str, x: int, y: int) -> str:
        pid = re.sub(r"[^a-z0-9_-]", "", str(pid).strip().lower())[:24]
        name = " ".join(str(name).split())[:80]
        if not pid or not name:
            raise WorldError("a place needs an id (letters, digits, - or _) and a name")
        x, y = int(x), int(y)
        world = self.load()
        if any(s["id"] == pid and not s.get("mine") for s in world["spots"]):
            raise WorldError(f"'{pid}' is one of the built-in places; pick another id")
        if world["tiles"].get(self._tile(world, x, y), {}).get("solid", True):
            raise WorldError(f"({x},{y}) is not floor; a place must be somewhere you can stand")
        ov = self.overlay()
        ov["spots"] = [s for s in ov["spots"] if s.get("id") != pid]
        if len(ov["spots"]) >= MAX_SPOTS:
            raise WorldError("too many named places; forget one first")
        room = self.room_at(world, x, y)
        ov["spots"].append({"id": pid, "name": name, "at": [x, y], "room": room["id"] if room else ""})
        self._commit(ov, f"name place {pid} at ({x},{y})")
        return f"'{pid}' ({name}) is now a place you can go, at ({x},{y})."

    def forget_place(self, pid: str) -> str:
        ov = self.overlay()
        keep = [s for s in ov["spots"] if s.get("id") != pid]
        if len(keep) == len(ov["spots"]):
            raise WorldError(f"'{pid}' is not one of the places you named")
        ov["spots"] = keep
        self._commit(ov, f"forget place {pid}")
        return f"Forgot the place '{pid}'."

    def map_text(self, room_id: str = "") -> str:
        """The map (or one room) as text with coordinates, for Tally to plan changes."""
        world = self.load()
        grid = world["map"]
        x0, y0, w, h = 0, 0, len(grid[0]), len(grid)
        if room_id:
            room = next((r for r in world["rooms"] if r["id"] == room_id), None)
            if not room:
                raise WorldError(f"no room '{room_id}'; rooms: {', '.join(r['id'] for r in world['rooms'])}")
            x0, y0, w, h = room["rect"]
        cols = range(x0, x0 + w)
        lines = ["     " + "".join(str(x // 10 % 10) for x in cols), "     " + "".join(str(x % 10) for x in cols)]
        for y in range(y0, y0 + h):
            lines.append(f"{y:>3}  " + grid[y][x0:x0 + w])
        used = sorted({grid[y][x] for y in range(y0, y0 + h) for x in cols})
        legend = ", ".join(f"{ch!s} {world['tiles'].get(world['legend'][ch], {}).get('name', world['legend'][ch])}"
                           for ch in used if ch in world["legend"])
        spots = [f"{s['id']} ({s['at'][0]},{s['at'][1]})" for s in world["spots"]
                 if x0 <= s["at"][0] < x0 + w and y0 <= s["at"][1] < y0 + h]
        rooms = "; ".join(f"{r['id']} x{r['rect'][0]}-{r['rect'][0] + r['rect'][2] - 1} "
                          f"y{r['rect'][1]}-{r['rect'][1] + r['rect'][3] - 1}" for r in world["rooms"])
        return "\n".join(lines + ["", f"Key: {legend}", f"Places: {', '.join(spots) or 'none'}",
                                  f"Rooms: {rooms}", f"You can place: {', '.join(MOVABLE)}",
                                  "Not on this plan: the cat and the errand imp. They move about in the game itself, "
                                  "so where the cat is comes with each message, in <game_state>."])

    # ---------- where Tally means to be ----------

    def set_intent(self, kind: str, spot: str = "", why: str = "", minutes: float = 0) -> str:
        """Where Tally means to be, and why. Without minutes it holds until the next tick asks again."""
        world = self.load()
        minutes = max(0.0, min(float(minutes or 0), 120.0))
        why = " ".join(str(why or "").split())[:200]
        intent = {"kind": kind, "minutes": minutes, "why": why, "time": time.time()}
        until = (f"for {minutes:g} minutes, then the game asks you again" if minutes
                 else "until the game next asks you (every few minutes), when you can keep it or change your mind")
        if kind == "go":
            s = next((s for s in world["spots"] if s["id"] == str(spot).strip().lower()), None)
            if not s:
                raise WorldError(f"no place '{spot}'; places: {', '.join(s['id'] for s in world['spots'])}")
            intent.update(spot=s["id"], at=s["at"], name=s["name"], room=s.get("room", ""))
            msg = f"You will walk to {s['name']} when this reply ends, and stay {until}."
        elif kind == "stay":
            msg = f"You will stay where you are {until}."
        elif kind == "follow":
            msg = f"You will walk with OWNER {until}."
        elif kind == "free":
            msg = "You are free to wander again."
        else:
            raise WorldError(f"unknown intent {kind}")
        _write_json(self.intent_file, intent)
        return msg

    def settle(self, how: str, why: str = "", item: str = "", place: str = "", minutes: float = 0) -> str:
        """Sit, lie or walk: on furniture of a kind, the nearest that suits, or the floor. Resolved by the game."""
        how = str(how).strip().lower()
        if how not in POSES:
            raise WorldError(f"how must be one of: {', '.join(POSES)}")
        item = str(item or "").strip().lower().replace(" ", "_")
        world = self.load()
        kinds = {tid for tid, d in world["tiles"].items() if how in (d.get("use") or {}).get("poses", [])}
        if item and item != "floor" and item not in kinds:
            raise WorldError(f"you cannot {how} on a {item}; for '{how}' try: {', '.join(sorted(kinds | ({'floor'} if how != 'walk' else set())))}")
        if item == "floor" and how == "walk":
            raise WorldError("walking in place needs a treadmill")
        minutes = max(0.0, min(float(minutes or 0), 120.0))
        intent = {"kind": "settle", "how": how, "item": item, "minutes": minutes,
                  "why": " ".join(str(why or "").split())[:200], "time": time.time()}
        where = "where you stand" if not place else ""
        if place:
            s = next((s for s in world["spots"] if s["id"] == str(place).strip().lower()), None)
            if not s:
                raise WorldError(f"no place '{place}'; places: {', '.join(s['id'] for s in world['spots'])}")
            intent.update(spot=s["id"], at=s["at"], name=s["name"], room=s.get("room", ""))
            where = s["name"]
        what = {"sit": "sit", "lie": "lie down", "walk": "walk in place"}[how]
        on = f"on the floor {where}" if item == "floor" else (f"on a {world['tiles'][item]['name']}" if item else "on the nearest thing that suits, or the floor")
        if place and item != "floor":
            on = f"{where}" + (f", on a {world['tiles'][item]['name']}" if item else ", on whatever is there, or the floor")
        _write_json(self.intent_file, intent)
        return f"You will {what} {on} when this reply ends, until the game next asks you" + (f" (at least {minutes:g} minutes)." if minutes else ".")

    def take_intent(self) -> dict | None:
        data = _read_json(self.intent_file, None)
        if data is not None:
            try:
                self.intent_file.unlink()
            except OSError:
                pass
        return data

    # ---------- where Tally is, kept across sessions ----------

    def save_tally(self, rec: dict) -> dict:
        """Keep where Tally is, sent by the game. Only known fields, cleaned, are kept."""
        def txt(v, n):
            return " ".join(str(v or "").split())[:n]

        def cell(v):
            if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, (int, float)) for c in v):
                return [int(v[0]), int(v[1])]
            return None

        at = cell([rec.get("x"), rec.get("y")])
        if not at:
            raise WorldError("Tally's position needs x and y")
        world = self.load()
        if not (0 <= at[1] < len(world["map"]) and 0 <= at[0] < len(world["map"][at[1]])):
            raise WorldError("that is outside the vault")
        d = rec.get("doing") or {}
        kind = d.get("kind") if d.get("kind") in ("round", "go", "stay", "follow") else "round"
        out = {"x": at[0], "y": at[1], "saved": time.time(),
               "doing": {"kind": kind, "why": txt(d.get("why"), 200), "name": txt(d.get("name"), 120),
                         "firm_until": float(d.get("firm_until") or 0)}}
        if rec.get("pose") in POSES:
            out["pose"] = rec["pose"]
        g = rec.get("goal") or {}
        if cell(g.get("at")):
            out["goal"] = {"at": cell(g["at"]), "name": txt(g.get("name"), 120), "room": txt(g.get("room"), 40)}
        plan = [p for p in (rec.get("plan") or [])[:3] if isinstance(p, dict) and cell(p.get("at"))]
        if plan:
            out["plan"] = [{"at": cell(p["at"]), "name": txt(p.get("name"), 120), "room": txt(p.get("room"), 40),
                            "why": txt(p.get("why"), 200)} for p in plan]
        _write_json(self.tally_file, out)
        return out

    def tally(self) -> dict | None:
        """Where Tally was when the game last saved, or None the first time."""
        return _read_json(self.tally_file, None)
