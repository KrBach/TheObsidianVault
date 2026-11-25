"""Tally's tick: the prompt is grounded in the map, and odd replies never move Tally anywhere strange."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from obsidian_vault.moves import MoveLog
from obsidian_vault.tick import Ticker, build_prompt, nearby, parse

ROOT = Path(__file__).resolve().parent.parent


def world() -> dict:
    w = json.loads((ROOT / "world" / "vault.json").read_text(encoding="utf-8"))
    w.update(json.loads((ROOT / "world" / "tiles.json").read_text(encoding="utf-8")))
    return w


class FakeBrain:
    def __init__(self, reply="", fail=False):
        self.reply, self.fail, self.lock, self.calls = reply, fail, threading.Lock(), []

    def muse(self, system, prompt, model):
        self.calls.append((system, prompt, model))
        if self.fail:
            raise RuntimeError("no claude here")
        return self.reply


class Cfg:
    tick_model = "haiku"

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)


class TickTest(unittest.TestCase):
    def setUp(self):
        self.w = world()

    def test_prompt_names_place_neighbours_and_spots(self):
        p = build_prompt(self.w, {"tally": {"x": 22, "y": 2}, "player": {"x": 26, "y": 5}, "here_minutes": 3})
        self.assertIn("(22,2) in The Hearth, by the still pool", p)
        self.assertIn("still pool", p.split("Near you:")[1].splitlines()[0])
        self.assertIn("(same room)", p)
        for s in self.w["spots"]:
            self.assertIn(f"- {s['id']}:", p)

    def test_nearby_says_your_desk(self):
        self.assertIn("your desk", nearby(self.w, 26, 3))

    def test_parse_spot_and_line(self):
        r = parse('Here: {"go": "Pool", "say": "  The pool  is quiet. "}', self.w)
        self.assertEqual((r["go"], r["at"], r["say"]), ("pool", [22, 2], "The pool is quiet."))

    def test_parse_unknown_or_garbage_stays(self):
        for reply in ("", "I'd like to go to the moon", '{"go": "moon"}', "{not json}"):
            self.assertEqual(parse(reply, self.w)["go"], "stay")
            self.assertIsNone(parse(reply, self.w)["at"])

    def test_spots_are_on_walkable_tiles(self):
        legend, tiles = self.w["legend"], self.w["tiles"]
        for s in self.w["spots"]:
            x, y = s["at"]
            self.assertFalse(tiles[legend[self.w["map"][y][x]]].get("solid"), s["id"])

    def test_ticker_skips_while_talking_and_survives_errors(self):
        brain = FakeBrain('{"go": "maproom", "say": ""}')
        t = Ticker(Cfg(), brain)
        with brain.lock:
            self.assertEqual(t.tick(self.w, {}).get("skipped"), "busy")
        self.assertEqual(t.tick(self.w, {})["go"], "maproom")
        self.assertEqual(brain.calls[0][2], "haiku")
        bad = Ticker(Cfg(), FakeBrain(fail=True)).tick(self.w, {})
        self.assertEqual(bad["go"], "stay")
        self.assertIn("no claude here", bad["error"])

    def test_parse_keeps_a_small_planned_round_of_real_places(self):
        r = parse('{"go": "pool", "why": "quiet", "then": [{"go": "maproom", "why": "check the indexes"},'
                  ' {"go": "moon"}, {"go": "desk"}]}', self.w)
        self.assertEqual([p["go"] for p in r["plan"]], ["maproom"])  # unknown places dropped, at most two looked at
        self.assertEqual(r["plan"][0]["why"], "check the indexes")

    def test_parse_rest(self):
        self.assertEqual(parse('{"go": "pool", "rest": "Sit"}', self.w)["rest"], "sit")
        self.assertEqual(parse('{"go": "stay", "rest": "lie"}', self.w)["rest"], "lie")
        self.assertEqual(parse('{"go": "pool", "rest": "dance"}', self.w)["rest"], "")

    def test_tick_choices_are_written_down_and_remembered(self):
        cfg = Cfg()
        brain = FakeBrain('{"go": "pool", "why": "the talk was long; I want the quiet", "say": "Quiet here.",'
                          ' "then": [{"go": "maproom", "why": "look at the new links"}]}')
        Ticker(cfg, brain).tick(self.w, {})
        memory = MoveLog(cfg.state_dir).for_prompt()
        self.assertIn("on your own, between conversations: you chose to go to by the still pool", memory)
        self.assertIn('Your reason: "the talk was long; I want the quiet"', memory)
        self.assertIn('Then you meant to go on to', memory)
        # the next tick is reminded of it
        Ticker(cfg, brain).tick(self.w, {})
        self.assertIn("Your own recent choices", brain.calls[-1][1])
        self.assertIn("the talk was long", brain.calls[-1][1])

    def test_talk_choices_and_old_entries(self):
        cfg = Cfg()
        log = MoveLog(cfg.state_dir)
        log.add("talk", "settle", "by the still pool", "rest my feet", how="sit", item="floor")
        self.assertIn("you chose to sit down on the floor, by the still pool", log.for_prompt())
        log.add("talk", "stay", "", "we are talking")
        self.assertIn("in a conversation with Kristian: you chose to stay where you were", log.for_prompt())
        old = json.dumps({"time": time.time() - 2 * 86400, "source": "tick", "kind": "go", "place": "x"})
        with open(log.path, "a", encoding="utf-8") as f:
            f.write(old + "\n")
        self.assertEqual(len(log.recent()), 1)  # a two-day-old move is not handed back


if __name__ == "__main__":
    unittest.main()
