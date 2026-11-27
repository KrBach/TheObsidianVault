"""The errand queue: errands OWNER approved "when there's room" start here, one at a time.

Plain code on a timer, never a model call. Every few minutes (and when a run ends) it looks at the
first errand in the queue (OWNER's priority, otherwise oldest first) and starts it only if:
- OWNER approved it (only approved errands are ever in the queue: the ledger puts them there);
- no other errand is running;
- its projected cost (the average of recent runs of its kind) fits what is left of the 5-hour block
  budget and of the daily cap, by the usage meter (usage.py).
Otherwise it waits, and the ledger, the notice board and queue_status say why and when the next check is.
Nothing here can approve, reorder or start an errand on anyone's say but OWNER's.

The last check is kept in <state_dir>/queue.json, so the vault tools (a separate process) can read it.
"""

from __future__ import annotations

import json
import threading
import time

from . import atomic
from .errands import ErrandError, Errands
from .usage import Meter, clock


class Scheduler:
    def __init__(self, cfg, errands: Errands, meter: Meter, clock_fn=time.time):
        self.cfg, self.errands, self.meter, self.clock = cfg, errands, meter, clock_fn
        self.file = cfg.state_dir / "queue.json"
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def interval(self) -> float:
        return max(30.0, float(getattr(self.cfg, "queue_check_minutes", 5.0) or 5.0) * 60)

    # ---------- the check ----------

    def check(self) -> dict:
        """Look for room once. Returns {"started": id or None, "waiting": id or None, "reason": ..., "next_check": t}."""
        if not self.lock.acquire(blocking=False):
            return {"started": None, "waiting": None, "reason": "a check is already under way", "skipped": True}
        try:
            now = self.clock()
            out = {"started": None, "waiting": None, "reason": "", "checked": now, "next_check": now + self.interval}
            queue = self.errands.scheduled()
            if queue:
                head = queue[0]
                out["waiting"] = head["id"]
                est, _ = self.errands.projection(head["kind"])
                if self.errands.running():
                    out["reason"] = "another errand is running; scheduled errands go one at a time"
                else:
                    ok, why = self.meter.room_for(est, self.meter.reading(fresh=True))
                    if not ok:
                        out["reason"] = why
                    else:
                        try:
                            self.errands.start_scheduled(head["id"])
                            out.update(started=head["id"], waiting=None)
                        except ErrandError as e:
                            out["reason"] = str(e)
            self._save(out)
            return out
        finally:
            self.lock.release()

    def _save(self, state: dict) -> None:
        try:
            atomic.write_text(self.file, json.dumps(state, ensure_ascii=False))
        except OSError:
            pass

    # ---------- the timer ----------

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception as e:  # noqa: BLE001 - the timer keeps going whatever one check does
                print(f"  queue check failed: {type(e).__name__}")
            self._stop.wait(self.interval)

    def poke(self) -> None:
        """Check soon, off the caller's thread (after an approval, or when a run ends)."""
        threading.Thread(target=self.check, daemon=True).start()


def last_check(cfg) -> dict:
    try:
        data = json.loads((cfg.state_dir / "queue.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def queue_view(cfg, errands: Errands, meter: Meter) -> dict:
    """The queue as data, for the ledger and the notice board."""
    tz = getattr(cfg, "timezone", "") or ""
    state = last_check(cfg)
    reading = meter.reading()
    nxt = state.get("next_check")

    def item(r: dict, n: int | None = None) -> dict:
        est, runs = errands.projection(r["kind"])
        d = {"id": r["id"], "kind": r["kind"], "title": r.get("title", ""), "status": r.get("status"),
             "estimate": round(est, 2), "estimate_runs": runs, "interrupted": int(r.get("interrupted") or 0)}
        if r.get("status") == "running":
            d["started"] = clock(float(r.get("started") or r.get("approved") or 0), tz)
        else:
            d["place"] = n
            if n == 1 and state.get("waiting") == r["id"] and state.get("reason"):
                d["waiting"] = state["reason"]
            d["next_check"] = clock(float(nxt), tz) if nxt else ""
        return d

    return {
        "usage": meter.line(reading),
        "running": [item(r) for r in errands.running()],
        "scheduled": [item(r, n) for n, r in enumerate(errands.scheduled(), 1)],
        "next_check": clock(float(nxt), tz) if nxt else "",
    }


def queue_status(cfg, errands: Errands, meter: Meter) -> str:
    """The queue in words, for Tally (read-only: nothing here can start or reorder an errand)."""
    v = queue_view(cfg, errands, meter)
    lines = [v["usage"]]
    if v["running"]:
        lines.append("Running:")
        lines += [f"- {r['title']} ({r['kind']}), started {r['started']}, projected about ${r['estimate']:.2f}."
                  for r in v["running"]]
    else:
        lines.append("Running: nothing.")
    if v["scheduled"]:
        lines.append("Scheduled (in the order they will start):")
        for r in v["scheduled"]:
            basis = f"average of {r['estimate_runs']}" if r["estimate_runs"] else "starting guess"
            line = f"{r['place']}. {r['title']} ({r['kind']}), about ${r['estimate']:.2f} ({basis})."
            if r.get("waiting"):
                line += f" Waiting for room, next check at {r['next_check']}: {r['waiting']}."
            elif r["place"] == 1 and r.get("next_check"):
                line += f" Next check at {r['next_check']}."
            if r["interrupted"]:
                line += " Back in the queue after a restart cut it off."
            lines.append(line)
    else:
        lines.append("Scheduled: nothing.")
    lines.append("Scheduled errands start on their own, one at a time, when the projected cost fits the block "
                 "budget and the daily cap. Only OWNER approves, reorders or starts them.")
    return "\n".join(lines)
