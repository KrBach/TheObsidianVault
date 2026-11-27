"""Brass, the Artificer: a temporary body on the map that follows the life of a code errand.

Scripted, no model calls. The server works out Brass's stage from the errand records (running, done,
failed); the browser walks him there.

- queued (propose_errand, kind "code"): nothing happens to him; he goes on pottering;
- approved: the imp hops off Tally's desk and runs to fetch him from wherever he is ("fetching");
  when it reaches him the browser calls fetched(), the imp leaves, and he walks to the Outbox table;
- running: he works at the Outbox table, tinkering;
- done or failed: he takes the report to the notice board, pauses, and goes back to his own business.

Between jobs he lives in the vault and potters about (the browser picks where; no model calls).
OWNER can talk to him (BrassTalk, below): one cheap model call per line, with his soul file.

There is one Brass. With several code errands he goes to the oldest one not yet finished with.
What he has seen is kept in <state_dir>/brass.json, so the game knows after a restart which errands
were his and where he should be (a running errand puts him straight back at the Outbox table).

The one-off parts (the imp's fetch, the walk to the board after an errand ends) are ended by the
browser calling fetched() or seen_walk(); if no browser is open to play them, they lapse after
ONE_OFF_SECONDS (a fetch into plain work, a report into nothing), so an old report does not bring
him to the board days later.

Where he stands comes from "brass" in world/vault.json. The placeholder look comes from
creatures.brass in world/tiles.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import atomic
from .config import REPO_ROOT

TALK_SKETCH = REPO_ROOT / "context" / "brass-talk.md"
SOUL = Path("denizens") / "brass" / "brass-soul.md"  # in the vault, relative to core_dir

NAME = "Brass"
ONE_OFF_SECONDS = 10 * 60  # a fetch or a walk to the board that no browser played out lapses after this
FORGET_SECONDS = 2 * 86400  # finished-with errands are dropped from brass.json after this
STATIONS = {"outbox": [42, 12], "board": [20, 12], "door": [26, 14]}
# stage -> where he ends up (while "fetching" he stays put until the imp reaches him)
GOES = {"fetching": "outbox", "working": "outbox", "reporting": "board"}
PLACE_WORDS = {"outbox": "the Outbox table", "board": "the notice board", "door": "the front door"}


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
        atomic.write_text(self.file, json.dumps(data, ensure_ascii=False))

    def update(self, records: list[dict]) -> dict:
        """Brass's state from the errand records (a code errand gets its record when OWNER approves it)."""
        now = self.clock()
        data = self._load()
        seen = data["errands"]
        before = json.dumps(data, sort_keys=True)
        recs = {r["id"]: r for r in records or [] if r.get("kind") == "code" and r.get("id")}

        def stage(eid: str, st: str, **kw) -> None:
            e = seen[eid]
            if e.get("stage") != st:
                e.update(stage=st, at=now, **kw)

        for eid, r in recs.items():
            status = r.get("status")
            if eid not in seen:
                if status != "running":
                    continue  # an errand that ended before Brass knew of it: not his to report
                # newly approved: the imp runs to fetch him
                seen[eid] = {"title": _title(r.get("title")), "since": float(r.get("approved") or now)}
                stage(eid, "fetching")
            if status in ("done", "failed") and seen[eid].get("stage") in ("fetching", "working"):
                stage(eid, "reporting", outcome=status)
        for eid, e in seen.items():
            if e.get("stage") in ("waiting", "rejected"):
                stage(eid, "closed")  # saved before the imp fetched him: he no longer waits at the desk
            if e.get("stage") == "fetching" and now - float(e.get("at") or now) > ONE_OFF_SECONDS:
                stage(eid, "working")  # no browser played the fetch: he simply got there
            if e.get("stage") == "reporting" and now - float(e.get("at") or now) > ONE_OFF_SECONDS:
                stage(eid, "closed")
        for eid in [k for k, e in seen.items()
                    if e.get("stage") == "closed" and now - float(e.get("at") or now) > FORGET_SECONDS]:
            del seen[eid]

        # one Brass: the oldest errand not yet finished with
        live = sorted(((e["since"], eid) for eid, e in seen.items()
                       if e.get("stage") in ("fetching", "working", "reporting")))
        if json.dumps(data, sort_keys=True) != before:
            self._write(data)
        if not live:
            return {"present": False, "name": NAME}
        eid = live[0][1]
        e = seen[eid]
        st = e["stage"]
        goes = GOES[st]
        # after the board: back to pottering, or straight on to the next errand if it is already running
        nxt = "outbox" if st == "reporting" and len(live) > 1 and seen[live[1][1]]["stage"] == "working" else ""
        out = {"present": True, "name": NAME, "errand": eid, "title": e["title"], "stage": st,
               "goes": goes, "at": self.places[goes], "then": nxt,
               "outcome": e.get("outcome", ""), "queue": len(live)}
        out["say"], out["going"] = self.phrases(out)
        return out

    def fetched(self, eid: str) -> None:
        """The browser played out the imp's fetch: Brass is on his way to the Outbox table."""
        data = self._load()
        e = data["errands"].get(str(eid))
        if e and e.get("stage") == "fetching":
            e.update(stage="working", at=self.clock())
            self._write(data)

    def seen_walk(self, eid: str) -> None:
        """The browser played out his walk to the notice board after a report: that errand is finished with."""
        data = self._load()
        e = data["errands"].get(str(eid))
        if e and e.get("stage") == "reporting":
            e.update(stage="closed", at=self.clock())
            self._write(data)

    # ---------- words ----------

    @staticmethod
    def phrases(st: dict) -> tuple[str, str]:
        """(when he is there, when he is on his way), for Tally's game_state."""
        t = st.get("title", "an errand")
        s = st.get("stage")
        if s == "fetching":
            return (f"The imp is running to fetch {NAME} for \"{t}\".",
                    f"The imp is running to fetch {NAME} for \"{t}\".")
        if s == "working":
            return (f"{NAME} is at the Outbox table, working on \"{t}\".",
                    f"{NAME} is on his way to the Outbox table to work on \"{t}\".")
        if s == "reporting":
            how = "failed" if st.get("outcome") == "failed" else "done"
            return (f"{NAME} is at the notice board with the report on \"{t}\" ({how}).",
                    f"{NAME} is on his way to the notice board with the report on \"{t}\" ({how}).")
        return ("", "")

    def for_prompt(self, st: dict, arrived: bool = True) -> str:
        if not st.get("present"):
            return ""
        say, going = self.phrases(st)
        return say if arrived else going


class BrassTalk:
    """Talking to Brass: one cheap model call per line (the tick's model), with his soul file and what
    he is doing. He can explain his work; he cannot start any (that stays with errands OWNER approves).
    The last few exchanges are kept in <state_dir>/brass-talk.json so he remembers the conversation."""

    KEEP = 6

    def __init__(self, cfg, brain):
        self.cfg, self.brain = cfg, brain
        self.file = Path(cfg.state_dir) / "brass-talk.json"

    def history(self) -> list[dict]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _remember(self, said: str, reply: str) -> None:
        talk = (self.history() + [{"said": said[:600], "reply": reply[:1200], "time": time.time()}])[-self.KEEP:]
        atomic.write_text(self.file, json.dumps(talk, ensure_ascii=False))

    def system(self) -> str:
        try:
            sketch = TALK_SKETCH.read_text(encoding="utf-8")
        except OSError:
            sketch = "You are Brass, the Artificer, the game's code-maker. Answer briefly."
        soul = ""
        core = getattr(self.cfg, "core_dir", None)
        if core:
            try:
                soul = (Path(core) / SOUL).read_text(encoding="utf-8")[:6000]
            except OSError:
                soul = ""
        text = sketch + (f"\n\n## Your soul file (your own words about how you work)\n\n{soul}" if soul else "")
        return getattr(self.cfg, "fill", lambda t: t)(text)

    def prompt(self, text: str, state: dict, errands: list[dict], summary) -> str:
        lines = []
        where = state.get("say") if state.get("present") else ""
        lines.append(f"Where you are: {where or 'between jobs, pottering about the vault.'}")
        current = next((r for r in errands if r.get("id") == state.get("errand")), None)
        if current:
            lines += ["", "The errand you are on:", summary(current, full=False)[:2500]]
        mine = [r for r in errands if r.get("kind") == "code"][:6]
        if mine:
            lines += ["", "Recent code errands (newest first):"]
            lines += [f"- {r.get('title', '')} ({r.get('status')})" for r in mine]
            last = next((r for r in mine if r.get("status") in ("done", "failed") and r is not current), None)
            if last:
                lines += ["", "Your last finished report:", summary(last, full=False)[:2000]]
        talk = self.history()
        if talk:
            lines += ["", "The conversation so far:"]
            for t in talk:
                lines += [f'- OWNER: "{t["said"]}"', f'  You: "{t["reply"]}"']
        lines += ["", f'OWNER says: "{text}"', "", "Answer as Brass, with your reply only."]
        return getattr(self.cfg, "fill", lambda t: t)("\n".join(lines))

    def reply(self, text: str, state: dict, errands: list[dict], summary) -> str:
        text = " ".join(str(text or "").split())[:600]
        if not text:
            raise ValueError("say something first")
        answer = self.brain.muse(self.system(), self.prompt(text, state, errands, summary),
                                 getattr(self.cfg, "tick_model", "haiku")).strip()
        answer = answer or "Brass grunts, which is not an answer."
        self._remember(text, answer)
        return answer
