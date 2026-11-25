"""Tally's own changes to the vault: laid over the map, checked by the map check, never in the repo."""

import json
import tempfile
import unittest
from pathlib import Path

from obsidian_vault.world import World, WorldError


class WorldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = World(Path(self.tmp.name) / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, x, y):
        world = self.w.load()
        return world["legend"][world["map"][y][x]]

    def test_furniture_can_be_placed_and_used(self):
        world = self.w.load()
        for item in ("chair", "bench", "couch", "hammock", "bed", "treadmill", "cushion"):
            self.assertIn("use", world["tiles"][item], item)
        self.w.place("bed", 30, 12)
        self.assertEqual(self.at(30, 12), "bed")
        # the built-in pieces stand where the spots say
        for sid, kind in (("couch", "couch"), ("hammock", "hammock"), ("pool_chair", "chair"), ("hall_bench", "bench")):
            spot = next(s for s in world["spots"] if s["id"] == sid)
            self.assertEqual(self.at(*spot["at"]), kind)

    def test_settle_intent(self):
        msg = self.w.settle("sit", "rest my feet", item="floor", place="pool")
        self.assertIn("on the floor by the still pool", msg)
        intent = self.w.take_intent()
        self.assertEqual((intent["kind"], intent["how"], intent["item"], intent["spot"]), ("settle", "sit", "floor", "pool"))
        self.w.settle("lie", "watch the notes", item="hammock")
        self.assertEqual(self.w.take_intent()["item"], "hammock")
        for bad in (("dance", ""), ("lie", "treadmill"), ("walk", "floor"), ("sit", "shelf")):
            with self.assertRaises(WorldError):
                self.w.settle(bad[0], "x", item=bad[1])
        with self.assertRaises(WorldError):
            self.w.settle("sit", "x", place="moon")

    def test_place_and_clear_a_candle(self):
        self.w.place("candle", 30, 12)
        self.assertEqual(self.at(30, 12), "candle")
        self.w.clear(30, 12)
        self.assertEqual(self.at(30, 12), "floor")
        log = (Path(self.tmp.name) / "state" / "world-journal.jsonl").read_text().splitlines()
        self.assertEqual(len(log), 2)

    def test_blocking_a_doorway_is_refused(self):
        # (34,10) is the door from the Entrance Hall to the Outbox; (33,10) is the floor in front of it
        with self.assertRaises(WorldError) as e:
            self.w.place("pool", 33, 10)
        self.assertIn("break the map", str(e.exception))
        self.assertEqual(self.at(33, 10), "floor")

    def test_fixed_things_and_bad_items(self):
        for args in (("shelf", 30, 12), ("candle", 0, 0), ("pool", 26, 3), ("pool", 26, 13)):
            with self.assertRaises(WorldError):
                self.w.place(*args)
        with self.assertRaises(WorldError):
            self.w.clear(26, 4)  # the desk
        with self.assertRaises(WorldError):
            self.w.place("brazier", 30, 11)  # braziers stand against a wall

    def test_named_places_and_intents(self):
        self.w.name_place("reading-nook", "by the new candle", 31, 12)
        spot = next(s for s in self.w.load()["spots"] if s["id"] == "reading-nook")
        self.assertEqual((spot["at"], spot["room"]), ([31, 12], "hall"))
        with self.assertRaises(WorldError):
            self.w.name_place("pool", "mine now", 31, 12)
        msg = self.w.set_intent("go", "reading-nook", "a quiet minute after the talk")
        self.assertIn("until the game next asks you", msg)
        intent = self.w.take_intent()
        self.assertEqual((intent["kind"], intent["at"], intent["why"]), ("go", [31, 12], "a quiet minute after the talk"))
        self.assertIsNone(self.w.take_intent())
        with self.assertRaises(WorldError):
            self.w.set_intent("go", "the moon")

    def test_tally_position_is_kept_across_sessions(self):
        self.assertIsNone(self.w.tally())
        self.w.save_tally({"x": 20, "y": 4, "pose": "lie", "evil": "x",
                           "doing": {"kind": "stay", "why": "  resting\n by the pool ", "firm_until": 0},
                           "plan": [{"at": [22, 3], "name": "by the chair", "why": "then read"}, {"at": "bad"}]})
        again = World(self.w.state)  # a new game, same state folder
        t = again.tally()
        self.assertEqual((t["x"], t["y"], t["pose"]), (20, 4, "lie"))
        self.assertEqual(t["doing"], {"kind": "stay", "why": "resting by the pool", "name": "", "firm_until": 0.0})
        self.assertEqual([p["at"] for p in t["plan"]], [[22, 3]])
        self.assertNotIn("evil", t)
        self.w.save_tally({"x": 1, "y": 1, "doing": {"kind": "rule the world"}, "pose": "dance"})
        self.assertEqual(self.w.tally()["doing"]["kind"], "round")
        self.assertNotIn("pose", self.w.tally())
        with self.assertRaises(WorldError):
            self.w.save_tally({"x": 9999, "y": 1})
        with self.assertRaises(WorldError):
            self.w.save_tally({"x": "a", "y": 1})

    def test_map_text_has_coordinates(self):
        text = self.w.map_text("hall")
        self.assertIn(" 12  ", text)
        self.assertIn("B the front desk bell", text)


if __name__ == "__main__":
    unittest.main()
