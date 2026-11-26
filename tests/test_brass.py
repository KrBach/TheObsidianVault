"""Brass's temporary body: which stage of a code errand he is at, and that it survives a restart.

The imp's fetch and Brass's walks are played in the browser (denizens.js); these tests cover what the
server tells it (brass.py): when the imp is out to fetch him, when he works, reports, and is done.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.brass import ONE_OFF_SECONDS, Brass  # noqa: E402
from obsidian_vault.check_map import check  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.ledger import Ledger  # noqa: E402

BRIEF = "Give Brass a body on the map that follows a code errand, and test it."


class Clock:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def rec(eid, status, title="Fix the bell", approved=2.0, kind="code"):
    return {"id": eid, "kind": kind, "title": title, "status": status, "approved": approved}


class BrassTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock()
        self.brass = Brass(self.dir, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, eid="a1", **kw):
        """Approve an errand and let the imp reach him."""
        self.brass.update([rec(eid, "running", **kw)])
        self.brass.fetched(eid)

    def test_nobody_home_without_code_errands(self):
        self.assertFalse(self.brass.update([])["present"])
        # research and session errands are not his
        self.assertFalse(self.brass.update([rec("s1", "running", kind="session")])["present"])

    def test_approved_the_imp_runs_to_fetch_him(self):
        st = self.brass.update([rec("a1", "running")])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("fetching", "outbox", [42, 12]))
        self.assertEqual(st["errand"], "a1")
        # while the imp is out, he is not at a desk or anywhere else: he waits where he is
        self.assertEqual(st["say"], "The imp is running to fetch Brass for \"Fix the bell\".")
        self.assertEqual(st["going"], st["say"])

    def test_fetched_he_works_at_the_outbox_table(self):
        self.brass.update([rec("a1", "running")])
        self.brass.fetched("a1")
        st = self.brass.update([rec("a1", "running")])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("working", "outbox", [42, 12]))
        self.assertEqual(st["say"], "Brass is at the Outbox table, working on \"Fix the bell\".")
        self.assertNotIn("imp", st["say"])
        self.brass.fetched("a1")  # a second word from the browser changes nothing
        self.assertEqual(self.brass.update([rec("a1", "running")])["stage"], "working")

    def test_a_fetch_nobody_played_lapses_into_work(self):
        self.brass.update([rec("a1", "running")])
        self.clock.t += ONE_OFF_SECONDS + 1
        self.assertEqual(self.brass.update([rec("a1", "running")])["stage"], "working")

    def test_done_he_goes_to_the_board_then_back_to_pottering(self):
        self.start()
        st = self.brass.update([rec("a1", "done")])
        self.assertEqual((st["stage"], st["goes"], st["then"], st["outcome"]), ("reporting", "board", "", "done"))
        self.assertIn("(done)", st["say"])
        self.brass.seen_walk("a1")
        self.assertFalse(self.brass.update([rec("a1", "done")])["present"])

    def test_failed_he_goes_to_the_board_too(self):
        self.start()
        st = self.brass.update([rec("a1", "failed")])
        self.assertEqual((st["stage"], st["outcome"]), ("reporting", "failed"))
        self.assertIn("(failed)", st["say"])

    def test_an_errand_that_ends_before_the_imp_reaches_him_is_still_reported(self):
        self.brass.update([rec("a1", "running")])
        st = self.brass.update([rec("a1", "failed")])
        self.assertEqual((st["stage"], st["goes"]), ("reporting", "board"))
        self.brass.fetched("a1")  # the imp arriving late does not undo the report
        self.assertEqual(self.brass.update([rec("a1", "failed")])["stage"], "reporting")

    def test_one_off_walks_lapse_if_no_browser_plays_them(self):
        self.start()
        self.brass.update([rec("a1", "done")])
        self.clock.t += ONE_OFF_SECONDS + 1
        self.assertFalse(self.brass.update([rec("a1", "done")])["present"])

    def test_old_finished_errands_do_not_bring_him_in(self):
        self.assertFalse(self.brass.update([rec("old", "done")])["present"])

    def test_several_errands_one_brass_at_the_oldest(self):
        self.start("old", title="First", approved=1.0)
        st = self.brass.update([rec("old", "running", "First", 1.0), rec("new", "running", "Second", 5.0)])
        self.assertEqual((st["errand"], st["goes"], st["queue"]), ("old", "outbox", 2))
        self.brass.fetched("new")
        # the first finishes: the board, then straight on to the one already running
        st = self.brass.update([rec("old", "done", "First", 1.0), rec("new", "running", "Second", 5.0)])
        self.assertEqual((st["errand"], st["goes"], st["then"]), ("old", "board", "outbox"))
        self.brass.seen_walk("old")
        st = self.brass.update([rec("old", "done", "First", 1.0), rec("new", "running", "Second", 5.0)])
        self.assertEqual((st["errand"], st["stage"]), ("new", "working"))

    def test_running_at_restart_puts_him_back_at_the_outbox_table(self):
        self.start()
        again = Brass(self.dir, clock=self.clock)  # a new game process, same state folder
        st = again.update([rec("a1", "running")])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("working", "outbox", [42, 12]))
        saved = json.loads((self.dir / "brass.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["errands"]["a1"]["stage"], "working")

    def test_a_fetch_survives_a_restart_and_ends_at_the_outbox_table(self):
        self.brass.update([rec("a1", "running")])
        again = Brass(self.dir, clock=self.clock)
        st = again.update([rec("a1", "running")])
        self.assertEqual((st["stage"], st["at"]), ("fetching", [42, 12]))  # the browser puts him straight there
        again.fetched("a1")
        self.assertEqual(Brass(self.dir, clock=self.clock).update([rec("a1", "running")])["stage"], "working")

    def test_a_desk_wait_saved_by_the_old_game_is_dropped(self):
        (self.dir / "brass.json").write_text(json.dumps(
            {"errands": {"q1": {"title": "Old", "since": 1.0, "stage": "waiting", "at": self.clock.t}}}), encoding="utf-8")
        self.assertFalse(self.brass.update([])["present"])

    def test_with_the_real_ledger_queue_then_approve(self):
        vault = self.dir / "vault"
        (vault / "games").mkdir(parents=True)
        (vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        cfg = Config(core_dir=vault, state_dir=self.dir / "state", writes_mode="review", writes_folders=["games"])
        records = []

        def start(op):  # stands in for Errands.start: an approved errand gets a running record
            records.append(rec(op["id"], "running", op["title"], approved=self.clock.t, kind=op["errand_kind"]))
            return "Errand started."

        led = Ledger(cfg, on_errand=start)
        r = led.propose_errand("code", "Brass's body", BRIEF)
        # queued: no walk to the desk, nothing for him to do yet
        self.assertFalse(self.brass.update(records)["present"])
        led.approve(r.id)
        st = self.brass.update(records)
        self.assertEqual((st["errand"], st["stage"]), (r.id, "fetching"))
        self.assertIn("The imp is running to fetch Brass", st["say"])

    def test_queued_then_rejected_he_never_moves(self):
        vault = self.dir / "vault"
        (vault / "games").mkdir(parents=True)
        (vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        cfg = Config(core_dir=vault, state_dir=self.dir / "state", writes_mode="review", writes_folders=["games"])
        led = Ledger(cfg, on_errand=lambda op: "started")
        r = led.propose_errand("code", "Brass's body", BRIEF)
        led.reject(r.id)
        self.assertFalse(self.brass.update([])["present"])


class BrassMapTest(unittest.TestCase):
    def setUp(self):
        self.world = json.loads((ROOT / "world" / "vault.json").read_text(encoding="utf-8"))
        self.tiles = json.loads((ROOT / "world" / "tiles.json").read_text(encoding="utf-8"))["tiles"]

    def test_his_places_pass_the_map_check(self):
        self.assertEqual(check(self.world, self.tiles), [])

    def test_no_place_at_the_desk_any_more(self):
        self.assertNotIn("desk", self.world["brass"])

    def test_a_place_by_the_bell_is_refused(self):
        self.world["brass"] = {**self.world["brass"], "board": [24, 13]}  # the bell is at (24,12)
        self.assertTrue(any("Brass" in p for p in check(self.world, self.tiles)))


class BrassTalkTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        from obsidian_vault.brass import BrassTalk
        self.dir = Path(tempfile.mkdtemp())
        core = self.dir / "vault"
        (core / "denizens" / "brass").mkdir(parents=True)
        (core / "denizens" / "brass" / "brass-soul.md").write_text("I work test-first.", encoding="utf-8")
        self.calls = []

        class Brain:
            def muse(inner, system, prompt, model):
                self.calls.append((system, prompt, model))
                return "  It's on the table.  "

        cfg = SimpleNamespace(state_dir=self.dir / "state", core_dir=core, tick_model="haiku",
                              fill=lambda t: t.replace("OWNER", "Robin"))
        self.talk = BrassTalk(cfg, Brain())

    def test_answers_with_his_soul_and_his_job_on_the_cheap_model(self):
        recs = [{"id": "e1", "kind": "code", "title": "Text parser", "status": "running"},
                {"id": "e0", "kind": "research", "title": "Chess", "status": "done"}]
        state = {"present": True, "errand": "e1", "say": "Brass is at the Outbox table, working on \"Text parser\"."}
        reply = self.talk.reply("how's it going?", state, recs, lambda r, full=False: f"Errand {r['id']}: {r['title']}")
        self.assertEqual(reply, "It's on the table.")
        system, prompt, model = self.calls[0]
        self.assertEqual(model, "haiku")
        self.assertIn("I work test-first.", system)
        self.assertIn("cannot start", system)
        self.assertIn("Robin", system)
        self.assertIn("Errand e1: Text parser", prompt)
        self.assertNotIn("Chess", prompt)  # only code errands are his
        self.assertIn('Robin says: "how\'s it going?"', prompt)

    def test_remembers_the_last_few_lines(self):
        for i in range(8):
            self.talk.reply(f"line {i}", {"present": False}, [], lambda r, full=False: "")
        hist = self.talk.history()
        self.assertEqual(len(hist), BrassTalkTest.keep())
        self.assertEqual(hist[-1]["said"], "line 7")
        self.assertIn('"line 7"', self.calls[-1][1])
        self.assertIn('"line 6"', self.calls[-1][1])

    @staticmethod
    def keep():
        from obsidian_vault.brass import BrassTalk
        return BrassTalk.KEEP

    def test_empty_line_is_refused(self):
        with self.assertRaises(ValueError):
            self.talk.reply("   ", {}, [], lambda r, full=False: "")


if __name__ == "__main__":
    unittest.main()
