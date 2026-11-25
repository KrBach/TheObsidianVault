import json
import tempfile
import unittest
from pathlib import Path

from obsidian_vault.cat import HUNGRY_AFTER, Cat, CatError
from obsidian_vault.tick import build_prompt, parse
from obsidian_vault.vault_mcp import call_game
from obsidian_vault.world import World


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class CatTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = Clock()
        self.roll = 0.9  # pets do not end in walking off unless a test says so
        self.cat = Cat(self.dir, rng=lambda: self.roll, clock=self.clock)
        self.cat.save({"x": 10, "y": 5, "mood": "sleepy", "asleep": False})

    def test_named_from_the_world_files(self):
        self.assertEqual(self.cat.name, "Vellum")

    def test_state_is_kept_and_hunger_comes_with_time(self):
        again = Cat(self.dir, clock=self.clock).get()
        self.assertEqual((again["x"], again["y"], again["mood"]), (10, 5, "sleepy"))
        self.assertFalse(again["hungry"])
        self.clock.t += HUNGRY_AFTER + 1
        later = Cat(self.dir, clock=self.clock).get()
        self.assertTrue(later["hungry"])
        self.assertEqual(later["mood"], "hungry")

    def test_feeding_resets_hunger_and_it_follows(self):
        self.clock.t += HUNGRY_AFTER + 1
        res = self.cat.tend("owner", "feed", [11, 5])
        self.assertEqual(res["effect"], "fed")
        s = self.cat.get()
        self.assertFalse(s["hungry"])
        self.assertEqual(s["mood"], "playful")

    def test_guests_may_pet_and_call_but_not_feed(self):
        self.assertTrue(self.cat.tend("guest", "pet", [11, 6])["ok"])
        with self.assertRaises(CatError):
            self.cat.tend("guest", "feed", [11, 6])
        with self.assertRaises(CatError):
            self.cat.tend("guest", "play", [11, 6])

    def test_a_guest_does_not_move_the_saved_cat(self):
        self.cat.tend("guest", "pet", [21, 6], cat={"x": 20, "y": 6})
        self.assertEqual((self.cat.get()["x"], self.cat.get()["y"]), (10, 5))

    def test_needs_to_be_next_to_it_except_to_call(self):
        far = self.cat.tend("owner", "pet", [14, 5])
        self.assertFalse(far["ok"])
        self.assertEqual(far["effect"], "none")
        self.assertEqual(self.cat.tend("owner", "call", [14, 5])["effect"], "come")
        self.assertFalse(self.cat.tend("owner", "call", [40, 5])["ok"])

    def test_pet_asleep_blinks_and_sometimes_it_walks_off(self):
        self.assertEqual(self.cat.tend("owner", "pet", [10, 6])["effect"], "purr")
        self.roll = 0.1
        self.assertEqual(self.cat.tend("owner", "pet", [10, 6])["effect"], "walk_off")
        self.cat.save({"x": 10, "y": 5, "mood": "sleepy", "asleep": True})
        self.assertEqual(self.cat.tend("owner", "pet", [10, 6])["effect"], "blink")
        self.assertEqual(self.cat.tend("owner", "call", [10, 6])["effect"], "none")

    def test_plays_only_when_playful(self):
        self.assertEqual(self.cat.tend("owner", "play", [10, 6])["effect"], "ignore")
        self.cat.save({"x": 10, "y": 5, "mood": "playful"})
        self.assertEqual(self.cat.tend("owner", "play", [10, 6])["effect"], "pounce")

    def test_tallys_tool_checks_where_tally_is_and_queues_the_effect(self):
        world = World(self.dir)
        (self.dir / "tally.json").write_text(json.dumps({"x": 30, "y": 5}))
        world.tally_file = self.dir / "tally.json"
        text = call_game(world, "tend_cat", {"action": "pet"})
        self.assertIn("next to", text)
        self.assertEqual(Cat(self.dir).take_pending(), [])
        world.tally_file.write_text(json.dumps({"x": 11, "y": 5}))
        text = call_game(world, "tend_cat", {"action": "pet"})
        self.assertIn("OWNER sees it", text)
        pending = Cat(self.dir).take_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["who"], "tally")
        self.assertEqual(Cat(self.dir).take_pending(), [])

    def test_the_tick_sees_the_cat_and_may_tend_it(self):
        world = {"spots": [], "rooms": [], "map": [], "legend": {}, "tiles": {}}
        prompt = build_prompt(world, {"tally": {"x": 11, "y": 5}}, cat=self.cat.for_prompt({"x": 11, "y": 5}))
        self.assertIn("Vellum", prompt)
        self.assertIn("right next to you", prompt)
        self.assertEqual(parse('{"go": "stay", "cat": "Pet"}', world).get("tend_cat"), "pet")
        self.assertNotIn("tend_cat", parse('{"go": "stay", "cat": "eat"}', world))


if __name__ == "__main__":
    unittest.main()
