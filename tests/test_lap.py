"""Tests for Vellum on a lap (cat.py, laps): the odds by tier, only a sleepy cat and only onto someone
sitting, hopping down when they move, the game_state words, and the parser's two lap lines.

The browser plays the lap out (web/js/denizens.js) with these rules and odds from the server.

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

from obsidian_vault.cat import (AFFINITY_FLOOR, Cat, hop_down_text, lap_odds, lap_ok, lap_text,  # noqa: E402
                                stays_on_lap, tier)
from obsidian_vault.names import fill  # noqa: E402
from obsidian_vault.vellum import WORDS_FILE, VellumParser  # noqa: E402
from obsidian_vault.vellum import tier as parser_tier  # noqa: E402

WORDS = json.loads(WORDS_FILE.read_text(encoding="utf-8"))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class LapRulesTest(unittest.TestCase):
    def test_tiers_are_the_parsers(self):
        t = WORDS["tiers"]
        for value in (0.0, AFFINITY_FLOOR, t["friendly_from"] - 0.001, t["friendly_from"], 0.5,
                      t["fond_from"] - 0.001, t["fond_from"], 1.0):
            self.assertEqual(tier(value), parser_tier(value, WORDS), value)
        self.assertEqual(tier(t["friendly_from"] - 0.001), "wary")
        self.assertEqual(tier(t["fond_from"]), "fond")

    def test_odds_by_tier(self):
        fond, friendly, wary = lap_odds(0.9), lap_odds(0.5), lap_odds(AFFINITY_FLOOR)
        self.assertEqual(wary, 0.0)  # never when wary
        self.assertGreater(friendly, 0.0)  # rarely when friendly ...
        self.assertLessEqual(friendly, 0.1)
        self.assertGreaterEqual(fond, 0.5)  # ... mostly when fond
        self.assertEqual(lap_odds(WORDS["tiers"]["friendly_from"] - 0.001), 0.0)
        self.assertEqual(lap_odds(WORDS["tiers"]["fond_from"]), fond)

    def test_only_sleepy_and_only_seated(self):
        for how in ("sit", {"how": "sit", "h": 9, "floor": False}, {"how": "sit", "floor": True}):
            self.assertTrue(lap_ok("sleepy", how))
        for mood in ("curious", "playful", "hungry"):
            self.assertFalse(lap_ok(mood, "sit"), mood)
        for how in (None, "", "lie", "walk", {"how": "walk"}, {}):
            self.assertFalse(lap_ok("sleepy", how), how)

    def test_hops_down_when_the_sitter_moves(self):
        sitting = {"x": 4, "y": 7, "pose": {"how": "sit", "h": 9}}
        self.assertTrue(stays_on_lap({"x": 4, "y": 7}, sitting))
        self.assertFalse(stays_on_lap({"x": 4, "y": 7}, {**sitting, "x": 5}))  # moved a step
        self.assertFalse(stays_on_lap({"x": 4, "y": 7}, {**sitting, "pose": None}))  # stood up
        self.assertFalse(stays_on_lap({"x": 4, "y": 7}, {**sitting, "pose": {"how": "walk"}}))  # walking
        self.assertFalse(stays_on_lap({"x": 4, "y": 7}, None))  # gone
        self.assertEqual(hop_down_text("Vellum"), "Vellum hops down, offended.")


class LapStateTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cat = Cat(Path(tempfile.mkdtemp()), rng=lambda: 0.9, clock=self.clock)
        self.cat.save({"x": 4, "y": 7, "mood": "sleepy", "asleep": False})
        self.n = self.cat.name

    def on_lap(self, who, x=4, y=7):
        return self.cat.save({"x": x, "y": y, "mood": "sleepy", "asleep": True, "lap": who})

    def test_lap_is_kept_only_while_asleep_and_only_for_tally_or_owner(self):
        self.assertEqual(self.on_lap("tally")["lap"], "tally")
        self.assertEqual(self.on_lap("owner")["lap"], "owner")
        self.assertEqual(self.on_lap("guest")["lap"], "")
        self.assertEqual(self.cat.save({"x": 4, "y": 7, "mood": "sleepy", "asleep": False, "lap": "tally"})["lap"], "")

    def test_odds_follow_affinity(self):
        self.assertEqual(self.cat.get()["lap_odds"], {"tally": 0.0, "owner": 0.0})  # it barely knows them
        for _ in range(12):
            self.cat.tend("owner", "feed", [4, 8])
        s = self.cat.get()
        self.assertEqual(tier(s["affinity"]["owner"]), "fond")
        self.assertEqual(s["lap_odds"]["owner"], lap_odds(0.9))
        self.assertEqual(s["lap_odds"]["tally"], 0.0)
        self.assertEqual(s["hop_down_text"], f"{self.n} hops down, offended.")

    def test_hopping_down_changes_no_affinity(self):
        for _ in range(3):
            self.cat.tend("tally", "feed", [4, 8])
        before = self.cat.get()["affinity"]
        self.on_lap("tally")
        after = self.cat.save({"x": 4, "y": 8, "mood": "sleepy", "asleep": False})  # hopped down beside them
        self.assertEqual(after["lap"], "")
        self.assertEqual(after["affinity"], before)

    def test_feeding_ends_the_lap(self):
        self.on_lap("owner")
        self.cat.tend("owner", "feed", [4, 7])
        self.assertEqual(self.cat.get()["lap"], "")

    def test_tend_cat_works_on_the_lap(self):
        self.on_lap("tally")
        res = self.cat.tend("tally", "pet", [4, 7])  # same cell: the lap
        self.assertTrue(res["ok"])
        self.assertEqual(res["effect"], "blink")
        self.on_lap("owner")
        self.assertTrue(self.cat.tend("tally", "pet", [5, 7])["ok"])  # Tally, next to OWNER's chair

    def test_game_state_words(self):
        self.assertEqual(lap_text("tally", "Vellum"), "Vellum is asleep on your lap.")
        self.assertEqual(fill(lap_text("owner", "Vellum"), owner="Robin"), "Vellum is asleep on Robin's lap.")
        self.on_lap("tally")
        self.assertIn(f"{self.n} is asleep on your lap", self.cat.for_prompt([4, 7]))
        self.on_lap("owner")
        line = fill(self.cat.for_prompt([5, 7]), owner="Robin")
        self.assertIn(f"{self.n} is asleep on Robin's lap", line)
        self.assertIn("right next to you", line)
        self.assertNotIn("your lap", line)
        self.cat.save({"x": 4, "y": 8, "mood": "sleepy", "asleep": True})
        self.assertNotIn("lap", self.cat.for_prompt([4, 7]))


class LapParserTest(unittest.TestCase):
    def setUp(self):
        self.cat = Cat(Path(tempfile.mkdtemp()), rng=lambda: 0.9, clock=Clock())
        self.p = VellumParser(self.cat, None, rng=lambda: 0.9, choose=lambda xs: xs[0])

    def test_look_and_take_on_owners_lap(self):
        self.cat.save({"x": 4, "y": 7, "mood": "sleepy", "asleep": True, "lap": "owner"})
        self.assertEqual(self.p.respond("> look", "owner", [4, 7])["text"], WORDS["lap"]["look"])
        self.assertEqual(WORDS["lap"]["look"], "Vellum is a warm, purring weight on your knees.")
        self.assertEqual(self.p.respond("> take vellum", "owner", [4, 7])["text"], WORDS["lap"]["take"])
        self.assertEqual(WORDS["lap"]["take"], "You are already holding Vellum. Technically Vellum is holding you.")

    def test_not_on_your_lap_reads_as_usual(self):
        self.cat.save({"x": 4, "y": 7, "mood": "sleepy", "asleep": True, "lap": "tally"})
        self.assertEqual(self.p.respond("> look", "owner", [4, 8])["text"], WORDS["look"]["sleepy"])
        self.assertNotEqual(self.p.respond("> take", "owner", [4, 8])["text"], WORDS["lap"]["take"])


if __name__ == "__main__":
    unittest.main()
