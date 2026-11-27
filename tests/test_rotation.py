"""Tests for closing conversations and the handoff (obsidian_vault/rotation.py): every cue gives Tally a
final turn and then a fresh session, the handoff survives a restart and is handed over once, the idle
timer starts again on input, and Quit stops the server.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.brains import Brain, ClaudeCodeBrain  # noqa: E402
from obsidian_vault.config import Config, load_config  # noqa: E402
from obsidian_vault.names import local_now  # noqa: E402
from obsidian_vault.persona import Persona  # noqa: E402
from obsidian_vault.rotation import CUES, HANDOFF_MAX_BYTES, Handoff, HandoffError, Rotation, day_start  # noqa: E402
from obsidian_vault.server import Game, make_handler  # noqa: E402
from obsidian_vault.vault_mcp import call_game  # noqa: E402
from obsidian_vault.world import World, WorldError  # noqa: E402


class FakeBrain(Brain):
    """Stands in for Claude Code. On a closing prompt it does what `closing` says:
    "write" (writes a handoff, as Tally would with write_handoff), "error", "slow" or "nothing"."""

    name = "fake"

    def __init__(self, state_dir: Path, closing: str = "write", session: str | None = "s1"):
        self.state_dir = Path(state_dir)
        self.closing = closing
        self.session_id = session
        self.context_tokens = 0
        self.per_turn = 1000
        self.prompts: list[str] = []
        self.count = 0
        self.cancelled = False
        self.release = threading.Event()

    def has_conversation(self) -> bool:
        return bool(self.session_id)

    def reset(self) -> None:
        self.session_id = None
        self.context_tokens = 0

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()

    def talk(self, text):
        self.prompts.append(text)
        self.count += 1
        if "<closing" in text:
            if self.closing == "write":
                Handoff(self.state_dir).write(f"handoff number {self.count}: the wishlist voice item is open")
                yield {"type": "delta", "text": "Goodnight."}
            elif self.closing == "error":
                Handoff(self.state_dir).write("half a handoff")  # written, then the turn failed
                yield {"type": "error", "text": "claude exited with code 1"}
            elif self.closing == "slow":
                self.release.wait(10)
                return  # cut off: no "done"
            yield {"type": "done"}
            return
        if not self.session_id:
            self.session_id = f"s{self.count}"
        self.context_tokens += self.per_turn
        yield {"type": "delta", "text": "ok"}
        yield {"type": "done"}


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_cfg(base: Path, **kw) -> Config:
    vault = base / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
    (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
    return Config(core_dir=vault, state_dir=base / "state", claude_path="", config_path=base / "config.toml", **kw)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = make_cfg(self.base)
        self.cfg.state_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def rot(self, brain=None, **kw) -> Rotation:
        return Rotation(self.cfg, brain or FakeBrain(self.cfg.state_dir), **kw)

    def test_every_cue_gives_a_final_turn_then_a_fresh_session(self):
        for cue in CUES:
            brain = FakeBrain(self.cfg.state_dir)
            res = self.rot(brain).close(cue)
            self.assertTrue(res["closed"] and res["handoff"], (cue, res))
            self.assertEqual(res["said"], "Goodnight.")
            self.assertIn(f'<closing cue="{cue}">', brain.prompts[-1])
            self.assertIn("write_handoff", brain.prompts[-1])
            self.assertIn("append_interaction_log", brain.prompts[-1])
            self.assertIsNone(brain.session_id, cue)  # the session ended
            self.assertIn("handoff number", Handoff(self.cfg.state_dir).read())

    def test_nothing_to_close_means_no_final_turn(self):
        brain = FakeBrain(self.cfg.state_dir, session=None)
        res = self.rot(brain).close("idle")
        self.assertFalse(res["closed"])
        self.assertEqual(brain.prompts, [])

    def test_a_failed_final_turn_closes_anyway_and_keeps_the_previous_handoff(self):
        Handoff(self.cfg.state_dir).write("the old handoff")
        brain = FakeBrain(self.cfg.state_dir, closing="error")
        rot = self.rot(brain)
        res = rot.close("quit")
        self.assertTrue(res["closed"])
        self.assertFalse(res["handoff"])
        self.assertIn("code 1", res["error"])
        self.assertIsNone(brain.session_id)
        self.assertEqual(Handoff(self.cfg.state_dir).read().strip(), "the old handoff")
        self.assertTrue(rot.state["handoff_pending"])  # the next conversation still gets the old one

    def test_a_final_turn_over_the_time_limit_is_cut_off(self):
        Handoff(self.cfg.state_dir).write("the old handoff")
        brain = FakeBrain(self.cfg.state_dir, closing="slow")
        started = time.monotonic()
        res = self.rot(brain, final_turn_seconds=0.2).close("idle")
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(res["closed"])
        self.assertIn("longer than", res["error"])
        self.assertTrue(brain.cancelled)
        self.assertIsNone(brain.session_id)
        self.assertEqual(Handoff(self.cfg.state_dir).read().strip(), "the old handoff")

    def test_the_handoff_survives_a_restart_and_is_handed_over_once(self):
        self.rot(FakeBrain(self.cfg.state_dir)).close("door")
        # the game restarts: a new Rotation reads the state folder
        rot = self.rot(FakeBrain(self.cfg.state_dir, session=None))
        text, injected = rot.first_turn_text("hello")
        self.assertTrue(injected)
        self.assertTrue(text.startswith("<handoff>"))
        self.assertIn("Your own note", text)
        self.assertIn('"door"', text)
        self.assertIn("handoff number", text)
        self.assertTrue(text.endswith("hello"))
        rot.turn_done(True, injected)
        self.assertEqual(rot.first_turn_text("again"), ("again", False))
        # and after another restart it is still handed over: no
        again = self.rot(FakeBrain(self.cfg.state_dir))
        self.assertEqual(again.first_turn_text("third"), ("third", False))

    def test_a_failed_first_turn_keeps_the_handoff_for_the_next_try(self):
        self.rot(FakeBrain(self.cfg.state_dir)).close("quit")
        rot = self.rot(FakeBrain(self.cfg.state_dir, session=None))
        _, injected = rot.first_turn_text("hello")
        rot.turn_done(False, injected)
        self.assertTrue(rot.first_turn_text("hello")[1])

    def test_idle_timer_starts_again_on_input(self):
        clock = Clock()
        brain = FakeBrain(self.cfg.state_dir)
        rot = self.rot(brain, clock=clock)
        clock.t += 29 * 60
        self.assertFalse(rot.idle_due())
        rot.touch()  # OWNER did something
        clock.t += 29 * 60
        self.assertFalse(rot.idle_due())
        clock.t += 2 * 60
        self.assertTrue(rot.idle_due())
        rot.begin_turn()  # a turn in progress is never cut off
        self.assertFalse(rot.idle_due())
        rot.end_turn()
        res = rot.check_idle()
        self.assertTrue(res and res["closed"])
        self.assertIn('cue="idle"', brain.prompts[-1])
        self.assertIsNone(rot.check_idle())  # nothing left to close
        self.cfg.idle_minutes = 0
        brain.session_id = "s2"
        clock.t += 10 * 3600
        self.assertFalse(rot.idle_due())  # 0: never

    def test_a_new_day_starts_at_four(self):
        self.assertEqual(day_start(datetime(2026, 11, 27, 3, 59)), datetime(2026, 11, 26, 4, 0))
        self.assertEqual(day_start(datetime(2026, 11, 27, 4, 0)), datetime(2026, 11, 27, 4, 0))
        self.assertEqual(day_start(datetime(2026, 11, 27, 23, 0)), datetime(2026, 11, 27, 4, 0))
        rot = self.rot(FakeBrain(self.cfg.state_dir))
        boundary = day_start(local_now()).timestamp()
        rot.state["last_turn"] = boundary - 60
        self.assertEqual(rot.due_before_message(), "day")
        rot.state["last_turn"] = boundary
        self.assertIsNone(rot.due_before_message())

    def test_a_long_conversation_rotates_past_the_size_limit(self):
        brain = FakeBrain(self.cfg.state_dir)
        rot = self.rot(brain)
        rot.state["last_turn"] = time.time()
        brain.context_tokens = 99_000
        self.assertIsNone(rot.due_before_message())
        self.assertIn("about 99K tokens", rot.size_line())
        brain.context_tokens = 150_000
        self.assertEqual(rot.due_before_message(), "size")
        self.cfg.context_limit_tokens = 0
        self.assertIsNone(rot.due_before_message())

    def test_the_handoff_tool(self):
        world = World(self.cfg.state_dir)
        msg = call_game(world, "write_handoff", {"text": "Open: the voice wish. Watch: Brass's PR."})
        self.assertIn("Handoff written", msg)
        self.assertIn("voice wish", Handoff(self.cfg.state_dir).read())
        with self.assertRaises(WorldError):
            call_game(world, "write_handoff", {"text": "x" * (HANDOFF_MAX_BYTES + 1)})
        with self.assertRaises(WorldError):
            call_game(world, "write_handoff", {"text": "  "})
        self.assertIn("voice wish", Handoff(self.cfg.state_dir).read())  # a refused one changes nothing
        with self.assertRaises(HandoffError):
            Handoff(self.cfg.state_dir).write("ø" * HANDOFF_MAX_BYTES)  # bytes, not characters

    def test_settings_for_rotation(self):
        p = self.base / "c.toml"
        p.write_text("[tally]\nidle_minutes = 45\ncontext_limit_tokens = 80000\n", encoding="utf-8")
        cfg = load_config(p)
        self.assertEqual((cfg.idle_minutes, cfg.context_limit_tokens), (45, 80000))
        p.write_text("", encoding="utf-8")
        cfg = load_config(p)
        self.assertEqual((cfg.idle_minutes, cfg.context_limit_tokens), (30, 100000))


FAKE_CLAUDE = """#!{python}
import json, sys, time
sys.stdin.read()
if "--sleep" in open({marker!r}).read():
    time.sleep(30)
print(json.dumps({{"type": "system", "session_id": "s9"}}))
print(json.dumps({{"type": "assistant", "session_id": "s9", "message": {{"usage": {{
    "input_tokens": 10, "cache_read_input_tokens": 120000, "cache_creation_input_tokens": 500, "output_tokens": 90}}}}}}))
print(json.dumps({{"type": "stream_event", "session_id": "s9", "event": {{"type": "content_block_delta",
    "delta": {{"type": "text_delta", "text": "hi"}}}}}}))
print(json.dumps({{"type": "result", "session_id": "s9"}}))
"""


class ClaudeBrainSizeTest(unittest.TestCase):
    """The Claude Code brain measures the context from the stream, keeps it with the session, and can be cut off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.marker = base / "mode"
        self.marker.write_text("", encoding="utf-8")
        exe = base / "claude"
        exe.write_text(FAKE_CLAUDE.format(python=sys.executable, marker=str(self.marker)), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        self.cfg = make_cfg(base)
        self.cfg.claude_path = str(exe)

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_size_from_the_stream(self):
        b = ClaudeCodeBrain(self.cfg, Persona("sys", ["soul.md"], []))
        events = list(b.talk("hello"))
        self.assertIn({"type": "delta", "text": "hi"}, events)
        self.assertEqual(b.context_tokens, 120_600)
        again = ClaudeCodeBrain(self.cfg, Persona("sys", ["soul.md"], []))  # after a restart
        self.assertEqual((again.session_id, again.context_tokens), ("s9", 120_600))
        self.assertTrue(again.has_conversation())
        again.reset()
        self.assertEqual((again.has_conversation(), again.context_tokens), (False, 0))

    def test_cancel_stops_a_turn(self):
        self.marker.write_text("--sleep", encoding="utf-8")
        b = ClaudeCodeBrain(self.cfg, Persona("sys", ["soul.md"], []))
        events = []
        t = threading.Thread(target=lambda: events.extend(b.talk("hello")))
        t.start()
        for _ in range(100):
            if b.proc:
                break
            time.sleep(0.02)
        b.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())


class ServerRotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.game = Game(make_cfg(base))
        self.game.auth.set_password("supersecret")
        self.brain = FakeBrain(self.game.cfg.state_dir)
        self.game.brain = self.game.rotation.brain = self.brain
        self.game.meter.line = lambda: ""  # the usage meter would read this machine's real Claude Code logs
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.game))
        self.game.server = self.server
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.owner = self.login("owner", "supersecret")

    def tearDown(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def raw(self, path, body, cookie):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def call(self, path, body, cookie):
        status, text = self.raw(path, body, cookie)
        return status, json.loads(text or "{}")

    def login(self, role, pw=""):
        req = urllib.request.Request(self.url + "/api/login", data=json.dumps({"role": role, "password": pw}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.headers.get("Set-Cookie", "").split(";")[0]

    def talk(self, text="hello"):
        status, body = self.raw("/api/talk", {"text": text, "where": "OWNER is in the Hearth."}, self.owner)
        self.assertEqual(status, 200)
        return [json.loads(line[6:]) for line in body.split("\n") if line.startswith("data: ")]

    def test_a_new_day_closes_the_old_conversation_before_the_message(self):
        rot = self.game.rotation
        rot.state["last_turn"] = day_start(local_now()).timestamp() - 3600  # before 04:00 today
        events = self.talk()
        self.assertTrue(any(e.get("type") == "action" and "fresh page" in e.get("text", "") for e in events))
        closing, first = self.brain.prompts[-2], self.brain.prompts[-1]
        self.assertIn('<closing cue="day">', closing)
        self.assertTrue(first.startswith("<handoff>"))
        self.assertIn("handoff number", first)
        self.assertIn("<game_state>OWNER is in the Hearth. This is a fresh conversation.</game_state>", first)
        self.talk("and again")  # the handoff is handed over once
        self.assertNotIn("<handoff>", self.brain.prompts[-1])
        self.assertIn("Context so far: about 1K tokens", self.brain.prompts[-1])

    def test_a_long_conversation_rotates_at_the_next_message(self):
        self.game.rotation.state["last_turn"] = time.time()
        self.brain.context_tokens = 120_000
        self.talk()
        self.assertIn('<closing cue="size">', self.brain.prompts[-2])
        self.assertTrue(self.brain.prompts[-1].startswith("<handoff>"))

    def test_a_short_conversation_just_goes_on(self):
        self.game.rotation.state["last_turn"] = time.time()
        self.talk()
        self.assertEqual(len(self.brain.prompts), 1)
        self.assertIn("Context so far: not measured yet.", self.brain.prompts[0])

    def test_new_conversation_closes_with_a_handoff(self):
        status, res = self.call("/api/reset", {}, self.owner)
        self.assertEqual(status, 200)
        self.assertTrue(res["closed"] and res["handoff"])
        self.assertIn('<closing cue="new">', self.brain.prompts[-1])

    def test_activity_starts_the_idle_timer_again(self):
        rot = self.game.rotation
        rot.last_input = 0
        self.assertTrue(rot.idle_due())
        self.assertEqual(self.call("/api/activity", {}, self.owner)[0], 200)
        self.assertFalse(rot.idle_due())
        rot.last_input = 0
        self.call("/api/activity", {}, self.login("guest"))  # a guest's input is not OWNER's
        self.assertTrue(rot.idle_due())

    def test_idle_settings_from_the_menu(self):
        status, res = self.call("/api/settings", {"idle_minutes": 45}, self.owner)
        self.assertEqual(status, 200, res)
        self.assertEqual((res["idle_minutes"], self.game.cfg.idle_minutes), (45, 45))
        self.assertEqual(load_config(self.game.cfg.config_path).idle_minutes, 45)
        for bad in (-1, "soon", 5000):
            self.assertEqual(self.call("/api/settings", {"idle_minutes": bad}, self.owner)[0], 400, bad)

    def test_guests_cannot_quit(self):
        self.assertEqual(self.call("/api/quit", {}, self.login("guest"))[0], 403)
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.brain.prompts, [])

    def test_quit_closes_the_conversation_and_stops_the_server(self):
        status, res = self.call("/api/quit", {"cue": "door"}, self.owner)
        self.assertEqual(status, 200)
        self.assertTrue(res["closed"] and res["handoff"], res)
        self.assertIn('<closing cue="door">', self.brain.prompts[-1])
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive(), "the server is still serving after Quit")
        self.assertTrue(json.loads((self.game.cfg.state_dir / "conversation.json").read_text())["handoff_pending"])


class DoorTest(unittest.TestCase):
    """The front door prompt lives in the browser (main.js); this checks the map it relies on and its wiring."""

    def test_the_door_and_the_mat(self):
        w = json.loads((ROOT / "world" / "vault.json").read_text(encoding="utf-8"))
        ex, ey = w["exit"]
        mx, my = w["spawn"]
        self.assertEqual((ex, ey), (26, 14))
        self.assertEqual(w["legend"][w["map"][ey][ex]], "door")
        self.assertEqual(w["legend"][w["map"][my][mx]], "mat")  # "no" steps back onto the mat, just inside
        self.assertEqual(abs(ex - mx) + abs(ey - my), 1)
        js = (ROOT / "web" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("atTheDoor(", js)
        self.assertIn("Leave the vault?", js)
        self.assertIn('leaveVault("door")', js)
        self.assertIn('leaveVault("quit")', js)
        self.assertIn('id="m-quit"', (ROOT / "web" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
