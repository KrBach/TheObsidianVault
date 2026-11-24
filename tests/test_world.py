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

    def test_map_text_has_coordinates(self):
        text = self.w.map_text("hall")
        self.assertIn(" 12  ", text)
        self.assertIn("B the front desk bell", text)


if __name__ == "__main__":
    unittest.main()
