"""Brass's temporary body: which stage of a code errand he is at, and that it survives a restart.

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


def op(eid, title="Fix the bell", created=1.0, kind="code"):
    return {"kind": "errand", "errand_kind": kind, "id": eid, "title": title, "created": created}


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

    def test_nobody_home_without_code_errands(self):
        self.assertFalse(self.brass.update([], [])["present"])
        # research and session errands are not his
        st = self.brass.update([op("r1", kind="research")], [rec("s1", "running", kind="session")])
        self.assertFalse(st["present"])

    def test_queued_he_waits_by_the_desk(self):
        st = self.brass.update([op("a1")], [])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("waiting", "desk", [28, 4]))
        self.assertEqual(st["errand"], "a1")
        self.assertIn("waiting for OWNER to approve \"Fix the bell\"", st["say"])

    def test_rejected_he_leaves_by_the_front_door_and_is_gone(self):
        self.brass.update([op("a1")], [])
        st = self.brass.update([], [])  # gone from the ledger, never started: rejected
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("leaving", "door", [26, 14]))
        self.brass.seen_walk("a1")
        self.assertFalse(self.brass.update([], [])["present"])

    def test_approved_he_works_at_the_outbox_table(self):
        self.brass.update([op("a1")], [])
        st = self.brass.update([], [rec("a1", "running")])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("working", "outbox", [42, 12]))
        self.assertEqual(st["say"], "Brass is at the Outbox table, working on \"Fix the bell\".")
        # between the pending list and the record (mid-approval) he does not take it for a rejection
        st = self.brass.update([op("a1")], [rec("a1", "running")])
        self.assertEqual(st["stage"], "working")

    def test_done_he_goes_to_the_board_then_out(self):
        self.brass.update([op("a1")], [])
        self.brass.update([], [rec("a1", "running")])
        st = self.brass.update([], [rec("a1", "done")])
        self.assertEqual((st["stage"], st["goes"], st["then"], st["outcome"]), ("reporting", "board", "door", "done"))
        self.assertIn("(done)", st["say"])
        self.brass.seen_walk("a1")
        self.assertFalse(self.brass.update([], [rec("a1", "done")])["present"])

    def test_failed_he_goes_to_the_board_too(self):
        self.brass.update([], [rec("a1", "running")])
        st = self.brass.update([], [rec("a1", "failed")])
        self.assertEqual((st["stage"], st["outcome"]), ("reporting", "failed"))
        self.assertIn("(failed)", st["say"])

    def test_one_off_walks_lapse_if_no_browser_plays_them(self):
        self.brass.update([], [rec("a1", "running")])
        self.brass.update([], [rec("a1", "done")])
        self.clock.t += ONE_OFF_SECONDS + 1
        self.assertFalse(self.brass.update([], [rec("a1", "done")])["present"])

    def test_old_finished_errands_do_not_bring_him_in(self):
        self.assertFalse(self.brass.update([], [rec("old", "done")])["present"])

    def test_several_errands_one_brass_at_the_oldest(self):
        st = self.brass.update([op("new", "Second", created=5.0), op("old", "First", created=1.0)], [])
        self.assertEqual((st["errand"], st["queue"]), ("old", 2))
        # the older one is approved: he works on it; the newer still waits
        st = self.brass.update([op("new", "Second", created=5.0)], [rec("old", "running", "First")])
        self.assertEqual((st["errand"], st["goes"]), ("old", "outbox"))
        # it finishes: the board, then on to the one still waiting rather than out of the door
        st = self.brass.update([op("new", "Second", created=5.0)], [rec("old", "done", "First")])
        self.assertEqual((st["errand"], st["goes"], st["then"]), ("old", "board", "desk"))
        self.brass.seen_walk("old")
        st = self.brass.update([op("new", "Second", created=5.0)], [rec("old", "done", "First")])
        self.assertEqual((st["errand"], st["goes"]), ("new", "desk"))

    def test_a_rejection_with_more_work_waiting_just_moves_him_on(self):
        self.brass.update([op("a", created=1.0), op("b", "Other", created=2.0)], [])
        st = self.brass.update([op("b", "Other", created=2.0)], [])  # a rejected, b still waits
        self.assertEqual((st["errand"], st["stage"]), ("b", "waiting"))

    def test_running_at_restart_puts_him_back_at_the_outbox_table(self):
        self.brass.update([op("a1")], [])
        self.brass.update([], [rec("a1", "running")])
        again = Brass(self.dir, clock=self.clock)  # a new game process, same state folder
        st = again.update([], [rec("a1", "running")])
        self.assertEqual((st["stage"], st["goes"], st["at"]), ("working", "outbox", [42, 12]))
        saved = json.loads((self.dir / "brass.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["errands"]["a1"]["stage"], "working")

    def test_with_the_real_ledger_queue_and_reject(self):
        vault = self.dir / "vault"
        (vault / "games").mkdir(parents=True)
        (vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        cfg = Config(core_dir=vault, state_dir=self.dir / "state", writes_mode="review", writes_folders=["games"])
        led = Ledger(cfg)
        r = led.propose_errand("code", "Brass's body", BRIEF)
        st = self.brass.update(led.pending(), [])
        self.assertEqual((st["errand"], st["goes"]), (r.id, "desk"))
        led.reject(r.id)
        self.assertEqual(self.brass.update(led.pending(), [])["goes"], "door")


class BrassMapTest(unittest.TestCase):
    def setUp(self):
        self.world = json.loads((ROOT / "world" / "vault.json").read_text(encoding="utf-8"))
        self.tiles = json.loads((ROOT / "world" / "tiles.json").read_text(encoding="utf-8"))["tiles"]

    def test_his_places_pass_the_map_check(self):
        self.assertEqual(check(self.world, self.tiles), [])

    def test_a_place_by_the_bell_is_refused(self):
        self.world["brass"] = {**self.world["brass"], "board": [24, 13]}  # the bell is at (24,12)
        self.assertTrue(any("Brass" in p for p in check(self.world, self.tiles)))


if __name__ == "__main__":
    unittest.main()
