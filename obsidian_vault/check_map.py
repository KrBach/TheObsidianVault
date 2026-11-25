"""Checks the map can be walked with the arrow keys alone.

Run: python3 -m obsidian_vault.check_map

- every walkable tile is reachable from the spawn point,
- every door has walkable tiles on two opposite sides, and each side leads into a room
  rather than a pocket (the way out of the vault is exempt),
- Tally can be reached (a walkable tile within talking range is reachable), and every
  post Tally walks between (tally_posts) and every named spot (spots) is reachable,
- Brass's places ("brass") each have a reachable cell to stand on that is clear of doorways,
  the bell, the welcome mat and the cat's cushion, and his door is the way out,
- lamps (tiles with "lamp": true) that block movement stand against a wall.

Only straight moves count, so nothing depends on squeezing diagonally past a corner.
Exits with status 1 and lists the problems if anything fails.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
MIN_ROOM = 4  # a door must open onto more than a pocket
TALK_RANGE = 2  # must match TALK_RANGE in web/js/main.js


def check(world: dict, tiles: dict) -> list[str]:
    grid, legend = world["map"], world["legend"]
    h, w = len(grid), max(len(r) for r in grid)
    problems = []

    if any(len(r) != w for r in grid):
        problems.append("map rows have different lengths")

    def tile(x, y):
        if not (0 <= y < h and 0 <= x < len(grid[y])):
            return None
        return legend.get(grid[y][x])

    def walkable(x, y):
        t = tile(x, y)
        return t is not None and t in tiles and not tiles[t]["solid"] and (x, y) != tuple(world["tally"])

    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch != " " and ch not in legend:
                problems.append(f"({x},{y}) {ch!r} is not in the legend")

    sx, sy = world["spawn"]
    if not walkable(sx, sy):
        problems.append(f"spawn ({sx},{sy}) is not walkable")
        return problems

    seen = {(sx, sy)}
    queue = deque([(sx, sy)])
    while queue:
        x, y = queue.popleft()
        for dx, dy in STEPS:
            n = (x + dx, y + dy)
            if n not in seen and walkable(*n):
                seen.add(n)
                queue.append(n)

    def region_size(start, avoid, cap=MIN_ROOM):
        """Walkable cells reachable from start without stepping on `avoid` (stops counting at cap)."""
        found, todo = {start}, [start]
        while todo and len(found) < cap:
            cx, cy = todo.pop()
            for dx, dy in STEPS:
                n = (cx + dx, cy + dy)
                if n != avoid and n not in found and walkable(*n):
                    found.add(n)
                    todo.append(n)
        return len(found)

    for y, row in enumerate(grid):
        for x, _ in enumerate(row):
            if walkable(x, y) and (x, y) not in seen:
                problems.append(f"({x},{y}) {tile(x, y)} cannot be reached from the spawn")
            if tile(x, y) == "door" and [x, y] != world.get("exit"):
                sides = [((x - 1, y), (x + 1, y)), ((x, y - 1), (x, y + 1))]
                pair = next((p for p in sides if walkable(*p[0]) and walkable(*p[1])), None)
                if not pair:
                    problems.append(f"door at ({x},{y}) is blocked on one side")
                    continue
                for side in pair:
                    if region_size(side, avoid=(x, y)) < MIN_ROOM:
                        problems.append(f"door at ({x},{y}) opens onto a dead end at {side}")

    tx, ty = world["tally"]
    talk_spots = [(tx + dx, ty + dy) for dx in range(-TALK_RANGE, TALK_RANGE + 1) for dy in range(-TALK_RANGE, TALK_RANGE + 1)]
    if not any(s in seen for s in talk_spots):
        problems.append("nobody can walk up to Tally")

    # Tally walks between posts; each must be a floor tile reachable from the spawn.
    for post in world.get("tally_posts", []):
        px, py = post["at"]
        if tuple(post["at"]) != tuple(world["tally"]) and (px, py) not in seen:
            problems.append(f"Tally's post in {post.get('room', '?')} at ({px},{py}) cannot be reached")
    for spot in world.get("spots", []):
        sx, sy = spot["at"]
        if tuple(spot["at"]) != tuple(world["tally"]) and (sx, sy) not in seen:
            problems.append(f"spot '{spot.get('id', '?')}' at ({sx},{sy}) cannot be reached")

    # Brass's places ("brass"): he comes and goes by the way out, and at the desk, the Outbox table and
    # the board he needs a reachable cell at or beside the anchor that is not in or by a doorway, by the
    # bell, on the welcome mat, on the cat's cushion or on the way in (the same rule as in denizens.js).
    brass = world.get("brass") or {}
    if brass and list(brass.get("door") or []) != list(world.get("exit") or []):
        problems.append("Brass's door must be the way out (exit)")
    fixed = {tuple(world["spawn"]), tuple(world["exit"]), tuple(world["tally"])}
    near8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]

    def brass_may_stand(x, y):
        if (x, y) not in seen or (x, y) in fixed or tile(x, y) in ("door", "mat", "cushion"):
            return False
        if any(tile(x + dx, y + dy) == "door" for dx, dy in STEPS):
            return False
        return not any(tile(x + dx, y + dy) == "bell" for dx, dy in near8)

    for name, at in brass.items():
        if name == "door":
            continue
        ax, ay = at
        if not any(brass_may_stand(ax + dx, ay + dy) for dx, dy in [(0, 0)] + near8):
            problems.append(f"Brass has nowhere to stand at his '{name}' place ({ax},{ay})")

    # Lamps and braziers that block movement must stand against a wall, out of the way.
    for y, row in enumerate(grid):
        for x, _ in enumerate(row):
            t = tile(x, y)
            d = tiles.get(t, {})
            if d.get("solid") and d.get("lamp"):
                if not any(tile(x + dx, y + dy) == "wall" for dx, dy in STEPS):
                    problems.append(f"{t} at ({x},{y}) blocks the floor; put it against a wall or make it non-solid")
    return problems


def main() -> int:
    world = json.loads((ROOT / "world" / "vault.json").read_text(encoding="utf-8"))
    tiles = json.loads((ROOT / "world" / "tiles.json").read_text(encoding="utf-8"))["tiles"]
    problems = check(world, tiles)
    for p in problems:
        print(f"map problem: {p}")
    if not problems:
        print("map ok: every floor tile and door is reachable with straight moves")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
