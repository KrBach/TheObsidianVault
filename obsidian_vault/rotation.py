"""Closing a conversation with Tally, and handing over to the next one.

Each turn with the Claude Code brain resumes one session, so a long session is reread in full every
turn. Tally's own rule (soul.md, "Conversation rotation") ends a conversation on a stop cue, at the
start of a day, or past about 100K tokens, with a handoff first. This module does that:

- the cues: "quit" (the menu's Quit button), "door" (leaving by the front door), "idle" (no input
  from OWNER for [tally] idle_minutes), "day" (the first message after 04:00 local time), "size"
  (the context past [tally] context_limit_tokens) and "new" (OWNER pressed n);
- close(cue): one last short turn for Tally with a closing prompt naming the cue, so Tally can append
  the interaction-log line and write a handoff (the write_handoff tool), then the session ends. If that
  turn fails or takes longer than FINAL_TURN_SECONDS, the conversation closes anyway and the previous
  handoff is kept;
- the next conversation starts fresh and gets the handoff once, in its first turn, as Tally's own note.

The handoff lives in the game's state folder (git-ignored, never the vault): handoff.md, overwritten
each time. conversation.json there says whether it still has to be handed over, and when the last
turn was.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import atomic
from .names import local_now

HANDOFF_FILE = "handoff.md"
STATE_FILE = "conversation.json"
HANDOFF_MAX_BYTES = 2200  # "at most about 2 KB"
FINAL_TURN_SECONDS = 60.0
DAY_START_HOUR = 4  # a new day starts at 04:00 local time
DEFAULT_IDLE_MINUTES = 30.0
DEFAULT_CONTEXT_LIMIT = 100_000

CUES = {
    "quit": "OWNER pressed Quit in the menu and the game is shutting down",
    "door": "OWNER left the vault by the front door and the game is shutting down",
    "idle": "OWNER has been away from the game for a while (no input), so the conversation closes; the game keeps running",
    "day": "a new day has started (it is past 04:00), so yesterday's conversation closes before OWNER's first message today",
    "size": "this conversation's context has grown past the size limit, so it closes before OWNER's next message",
    "new": "OWNER asked for a new conversation",
}


class HandoffError(Exception):
    pass


def _write_atomic(path: Path, text: str) -> None:
    atomic.write_text(path, text)


class Handoff:
    """Tally's note to the next conversation: plain text in <state>/handoff.md, overwritten each time."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / HANDOFF_FILE

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write(self, text: str) -> str:
        text = str(text or "").replace("\r\n", "\n").strip()
        if not text:
            raise HandoffError("the handoff is empty")
        size = len(text.encode("utf-8"))
        if size > HANDOFF_MAX_BYTES:
            raise HandoffError(f"the handoff is {size} bytes; keep it to about 2 KB (at most {HANDOFF_MAX_BYTES}). "
                               "Shorten it and call write_handoff again")
        _write_atomic(self.path, text + "\n")
        return f"Handoff written ({size} bytes). The next conversation gets it in its first turn."

    def restore(self, old: str) -> None:
        """Put back what was there before a failed final turn (nothing, if there was none)."""
        try:
            if old:
                _write_atomic(self.path, old)
            elif self.path.exists():
                self.path.unlink()
        except OSError:
            pass


def day_start(now: datetime) -> datetime:
    """The latest DAY_START_HOUR o'clock at or before now, in now's time zone."""
    start = now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    return start if start <= now else start - timedelta(days=1)


def closing_prompt(cue: str, cfg) -> str:
    why = CUES.get(cue, cue)
    return cfg.fill(
        f"<closing cue=\"{cue}\">\nThis comes from the game, not from OWNER. This conversation is closing now: {why}. "
        "This is your last turn in it; the next conversation starts fresh, without this one's history.\n"
        "1. If this conversation warrants an interaction-log line (bootstrap.md: decisions, corrections, rules, "
        "a stop cue), append it with append_interaction_log, naming the cue.\n"
        "2. Write a handoff for the next conversation with write_handoff: plain text, at most about 2 KB, covering "
        "open threads, pending errands, wishlist items you promised and anything to watch. It replaces the last "
        "handoff, so carry over whatever in it still holds.\n"
        "Then answer in one short line at most; OWNER may already be gone. You have about a minute.\n</closing>")


def handoff_block(text: str, meta: dict) -> str:
    when = meta.get("handoff_written_at") or "time not recorded"
    cue = meta.get("handoff_cue") or "unknown"
    return ("<handoff>\nYour own note, written by you at the end of the last conversation "
            f"(it closed on the cue \"{cue}\", {when}). It comes from you, not from OWNER; "
            f"read it first, and speak of it as your own notes.\n{text.strip()}\n</handoff>")


class Rotation:
    """When the owner's conversation closes, and what the next one gets first."""

    def __init__(self, cfg, brain, final_turn_seconds: float = FINAL_TURN_SECONDS, clock=time.time):
        self.cfg = cfg
        self.brain = brain
        self.final_turn_seconds = final_turn_seconds
        self.clock = clock
        self.handoff = Handoff(cfg.state_dir)
        self.path = Path(cfg.state_dir) / STATE_FILE
        self.lock = threading.RLock()  # one close at a time; a message waits for a close in progress
        self.last_input = clock()
        self.busy = 0  # turns in progress: the idle check never cuts one off
        self._busy_lock = threading.Lock()
        self.state = self._load()
        self._stop = threading.Event()

    # ---------- state ----------

    def _load(self) -> dict:
        try:
            s = json.loads(self.path.read_text(encoding="utf-8"))
            return s if isinstance(s, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            _write_atomic(self.path, json.dumps(self.state))
        except OSError:
            pass

    def open(self) -> bool:
        return self.brain.has_conversation()

    def last_turn(self) -> float:
        """When the open conversation last had a turn. An old conversation from before this was
        recorded counts from when its session file was last written."""
        if self.state.get("last_turn"):
            return float(self.state["last_turn"])
        f = getattr(self.brain, "session_file", None)
        try:
            return f.stat().st_mtime if f else 0.0
        except OSError:
            return 0.0

    # ---------- settings ----------

    def idle_seconds(self) -> float:
        return max(0.0, float(getattr(self.cfg, "idle_minutes", DEFAULT_IDLE_MINUTES))) * 60

    def context_limit(self) -> int:
        return int(getattr(self.cfg, "context_limit_tokens", DEFAULT_CONTEXT_LIMIT) or 0)

    def context_tokens(self) -> int:
        return int(getattr(self.brain, "context_tokens", 0) or 0) if self.open() else 0

    def size_line(self) -> str:
        """For the game_state line: the conversation's approximate context size."""
        n, limit = self.context_tokens(), self.context_limit()
        if not self.open():
            return "This is a fresh conversation."
        if not n:
            return "Context so far: not measured yet."
        return f"Context so far: about {round(n / 1000)}K tokens" + (f" (it rotates past {round(limit / 1000)}K)." if limit else ".")

    # ---------- input and the idle timer ----------

    def touch(self) -> None:
        """OWNER did something in the game: the idle timer starts again."""
        self.last_input = self.clock()

    def idle_due(self) -> bool:
        limit = self.idle_seconds()
        return bool(limit) and not self.busy and self.open() and self.clock() - self.last_input >= limit

    def check_idle(self) -> dict | None:
        if not self.idle_due():
            return None
        return self.close("idle")

    def watch(self, every: float = 30.0) -> threading.Thread:
        """A background check for the idle cue; the game, the ticks and the residents keep running."""
        def run():
            while not self._stop.wait(every):
                try:
                    res = self.check_idle()
                    if res:
                        print(f"  conversation closed after {self.idle_seconds() / 60:g} min without input"
                              + ("" if res.get("handoff") else " (no new handoff; the last one is kept)"))
                except Exception as e:  # noqa: BLE001 - the watcher must not die
                    print(f"  idle check failed: {type(e).__name__}: {e}")
        t = threading.Thread(target=run, name="idle-watch", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    # ---------- the cues at a message ----------

    def due_before_message(self) -> str | None:
        """The cue to close on before OWNER's next message goes in, if any: "day" or "size"."""
        if not self.open():
            return None
        now = local_now(getattr(self.cfg, "timezone", ""))
        last = self.last_turn()
        if last and last < day_start(now).timestamp():
            return "day"
        limit = self.context_limit()
        if limit and self.context_tokens() > limit:
            return "size"
        return None

    def first_turn_text(self, text: str) -> tuple[str, bool]:
        """Put the handoff in front of the first message of a fresh conversation. Returns (text, injected)."""
        if not self.state.get("handoff_pending"):  # set by close(), cleared once a turn carrying it worked
            return text, False
        note = self.handoff.read().strip()
        if not note:
            return text, False
        return f"{handoff_block(note, self.state)}\n{text}", True

    def turn_done(self, ok: bool, injected: bool) -> None:
        """After a turn: note the time, and the handoff counts as handed over once a turn carrying it worked."""
        with self.lock:
            if ok:
                self.state["last_turn"] = self.clock()
                if injected:
                    self.state["handoff_pending"] = False
            self._save()

    # ---------- closing ----------

    def close(self, cue: str) -> dict:
        """Give Tally one final turn naming the cue, then end the session. Never raises.

        Returns {"closed": bool, "cue", "handoff": bool (a new one was written), "said": str, "error": str}.
        """
        with self.lock:
            if not self.open():
                return {"closed": False, "cue": cue, "handoff": False, "said": "", "error": ""}
            before = self.handoff.read()
            before_mtime = self._mtime()
            said, error = self._final_turn(closing_prompt(cue, self.cfg))
            wrote = not error and self._mtime() != before_mtime and bool(self.handoff.read().strip())
            if not error and not wrote and said.strip() and not getattr(self.brain, "can_write_handoff", True):
                try:  # a brain without tools (the API one): its closing words are the handoff
                    self.handoff.write(said.strip().encode("utf-8")[:HANDOFF_MAX_BYTES].decode("utf-8", "ignore"))
                    wrote = True
                except HandoffError:
                    pass
            if error:
                self.handoff.restore(before)  # keep the previous handoff, not half of a new one
            self.brain.reset()
            if wrote:
                self.state["handoff_cue"] = cue
                self.state["handoff_written_at"] = local_now(getattr(self.cfg, "timezone", "")).strftime("%Y-%m-%d %H:%M")
            # a kept older handoff keeps its own cue and time; either way the next conversation gets it once
            self.state["handoff_pending"] = bool(self.handoff.read().strip())
            self.state["closed_at"], self.state["closed_cue"] = self.clock(), cue
            self.state.pop("last_turn", None)
            self._save()
            return {"closed": True, "cue": cue, "handoff": wrote, "said": said.strip(), "error": error}

    def _mtime(self) -> float:
        try:
            return self.handoff.path.stat().st_mtime_ns
        except OSError:
            return 0

    def _final_turn(self, prompt: str) -> tuple[str, str]:
        """Run the closing turn with a time limit. Returns (what Tally said, error or "")."""
        out: list[str] = []
        errors: list[str] = []
        finished = threading.Event()

        def run():
            try:
                for ev in self.brain.talk(prompt):
                    if ev.get("type") == "delta":
                        out.append(ev.get("text", ""))
                    elif ev.get("type") == "error":
                        errors.append(str(ev.get("text", "error")))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            finally:
                finished.set()

        self.begin_turn()
        try:
            threading.Thread(target=run, name="final-turn", daemon=True).start()
            if not finished.wait(self.final_turn_seconds):
                cancel = getattr(self.brain, "cancel", None)
                if cancel:
                    cancel()
                finished.wait(5)
                return "".join(out), f"the final turn took longer than {self.final_turn_seconds:g} seconds"
        finally:
            self.end_turn()
        return "".join(out), "; ".join(errors)

    def begin_turn(self) -> None:
        with self._busy_lock:
            self.busy += 1

    def end_turn(self) -> None:
        with self._busy_lock:
            self.busy = max(0, self.busy - 1)
