"""Brass, the Artificer: a temporary body on the map that follows the life of a code errand.

Scripted, no model calls. The server works out Brass's stage from the ledger (code errands waiting
for approval) and the errand records (running, done, failed); the browser walks him there.

- queued (propose_errand, kind "code"): he comes in by the front door and stands by Tally's desk;
- rejected: he walks back out of the front door;
- approved and running: he works at the Outbox table, tinkering;
- done or failed: he takes the report to the notice board, pauses, and leaves by the front door.

There is one Brass. With several code errands he goes to the oldest one not yet finished with.
What he has seen is kept in <state_dir>/brass.json, so the game knows after a restart which errands
were his and where he should be (a running errand puts him straight back at the Outbox table).

The one-off walks (to the board after an errand ends, out of the door after a rejection) are ended
by the browser calling seen(); if no browser is open to play them, they lapse after ONE_OFF_SECONDS,
so an old report does not bring him in days later.

Where he stands comes from "brass" in world/vault.json. The placeholder look comes from
creatures.brass in world/tiles.json.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import REPO_ROOT

NAME = "Brass"
ONE_OFF_SECONDS = 10 * 60  # a walk to the board or the door that no browser played out lapses after this
FORGET_SECONDS = 2 * 86400  # finished-with errands are dropped from brass.json after this
STATIONS = {"desk": [28, 4], "outbox": [42, 12], "board": [20, 12], "door": [26, 14]}
# stage -> where he goes
GOES = {"waiting": "desk", "working": "outbox", "reporting": "board", "leaving": "door"}
PLACE_WORDS = {"desk": "your desk", "outbox": "the Outbox table", "board": "the notice board",
               "door": "the front door"}


def stations() -> dict:
    """Brass's places, from world/vault.json ("brass"), falling back to the built-in ones."""
    out = {k: list(v) for k, v in STATIONS.items()}
    try:
        data = json.loads((REPO_ROOT / "world" / "vault.json").read_text(encoding="utf-8")).get("brass") or {}
        for k in out:
            v = data.get(k)
            if isinstance(v, list) and len(v) == 2:
                out[k] = [int(v[0]), int(v[1])]
    except (OSError, ValueError, TypeError):
        pass
    return out


def _title(s) -> str:
    return " ".join(str(s or "").split())[:80] or "an errand"


class Brass:
    def __init__(self, state_dir: Path, clock=time.time):
        self.file = Path(state_dir) / "brass.json"
        self.clock = clock
        self.places = stations()

    # ---------- state ----------

    def _load(self) -> dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("errands"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"errands": {}}

    def _write(self, data: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(f".{self.file.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)

    def update(self, pending: list[dict], records: list[dict]) -> dict:
        """Brass's state from the ledger's pending ops and the errand records.
        Read the pending ops before the records: an approval moves an errand from the first to the
        second, and read the other way round it could look like a rejection for a moment."""
        now = self.clock()
        data = self._load()
        seen = data["errands"]
        before = json.dumps(data, sort_keys=True)
        waiting = {op["id"]: op for op in pending or []
                   if op.get("kind") == "errand" and op.get("errand_kind") == "code" and op.get("id")}
        recs = {r["id"]: r for r in records or [] if r.get("kind") == "code" and r.get("id")}

        def stage(eid: str, st: str, **kw) -> None:
            e = seen[eid]
            if e.get("stage") != st:
                e.update(stage=st, at=now, **kw)

        for eid, op in waiting.items():
            if eid not in recs:
                seen.setdefault(eid, {"title": _title(op.get("title")), "since": float(op.get("created") or now)})
                stage(eid, "waiting")
        for eid, r in recs.items():
            status = r.get("status")
            if eid not in seen:
                if status != "running":
                    continue  # an errand that ended before Brass knew of it: not his to report
                seen[eid] = {"title": _title(r.get("title")), "since": float(r.get("approved") or now)}
            e = seen[eid]
            if status == "running":
                stage(eid, "working")
            elif status in ("done", "failed") and e.get("stage") in ("waiting", "working"):
                stage(eid, "reporting", outcome=status)
        for eid, e in seen.items():
            if e.get("stage") == "waiting" and eid not in waiting and eid not in recs:
                stage(eid, "rejected")
            if e.get("stage") in ("reporting", "rejected") and now - float(e.get("at") or now) > ONE_OFF_SECONDS:
                stage(eid, "closed")
        for eid in [k for k, e in seen.items()
                    if e.get("stage") == "closed" and now - float(e.get("at") or now) > FORGET_SECONDS]:
            del seen[eid]

        # one Brass: the oldest errand not yet finished with. A rejection with more work waiting is
        # simply over (he goes on to the next); a lone one walks him out.
        live = sorted(((e["since"], eid) for eid, e in seen.items()
                       if e.get("stage") in ("waiting", "working", "reporting", "rejected")))
        while len(live) > 1 and seen[live[0][1]]["stage"] == "rejected":
            stage(live.pop(0)[1], "closed")
        if json.dumps(data, sort_keys=True) != before:
            self._write(data)
        if not live:
            return {"present": False, "name": NAME}
        eid = live[0][1]
        e = seen[eid]
        st = "leaving" if e["stage"] == "rejected" else e["stage"]
        goes = GOES[st]
        nxt = "door"
        if st == "reporting" and len(live) > 1:
            nxt = GOES[seen[live[1][1]]["stage"]] if seen[live[1][1]]["stage"] != "rejected" else "door"
        out = {"present": True, "name": NAME, "errand": eid, "title": e["title"], "stage": st,
               "goes": goes, "at": self.places[goes], "then": nxt if st == "reporting" else "",
               "outcome": e.get("outcome", ""), "queue": len(live)}
        out["say"], out["going"] = self.phrases(out)
        return out

    def seen_walk(self, eid: str) -> None:
        """The browser played out a one-off walk (the board after a report, or the door after a
        rejection): that errand is finished with."""
        data = self._load()
        e = data["errands"].get(str(eid))
        if e and e.get("stage") in ("reporting", "rejected"):
            e.update(stage="closed", at=self.clock())
            self._write(data)

    # ---------- words ----------

    @staticmethod
    def phrases(st: dict) -> tuple[str, str]:
        """(when he is there, when he is on his way), for Tally's game_state."""
        t = st.get("title", "an errand")
        s = st.get("stage")
        if s == "waiting":
            return (f"{NAME} is standing by your desk, waiting for OWNER to approve \"{t}\".",
                    f"{NAME} is on his way to your desk about \"{t}\".")
        if s == "working":
            return (f"{NAME} is at the Outbox table, working on \"{t}\".",
                    f"{NAME} is on his way to the Outbox table to work on \"{t}\".")
        if s == "reporting":
            how = "failed" if st.get("outcome") == "failed" else "done"
            return (f"{NAME} is at the notice board with the report on \"{t}\" ({how}).",
                    f"{NAME} is on his way to the notice board with the report on \"{t}\" ({how}).")
        if s == "leaving":
            return (f"{NAME} is leaving by the front door.", f"{NAME} is leaving by the front door.")
        return ("", "")

    def for_prompt(self, st: dict, arrived: bool = True) -> str:
        if not st.get("present"):
            return ""
        say, going = self.phrases(st)
        return say if arrived else going
