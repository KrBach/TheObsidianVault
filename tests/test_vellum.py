"""Tests for the Vellum text parser (obsidian_vault/vellum.py): the words come from
world/vellum-parser.json, the rules from cat.py, and a '>' line never reaches a model.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.cat import HUNGRY_AFTER, Cat, next_to, steps  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.server import Game, make_handler  # noqa: E402
from obsidian_vault.vellum import WORDS_FILE, VellumParser, tier  # noqa: E402

WORDS = json.loads(WORDS_FILE.read_text(encoding="utf-8"))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Roll:
    def __init__(self, v=0.9):
        self.v = v

    def __call__(self):
        return self.v


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = Clock()
        self.cat_roll = Roll(0.9)  # pets do not end in walking off unless a test says so
        self.gift_roll = Roll(0.9)  # and no gift unless a test says so
        self.cat = Cat(self.dir, rng=self.cat_roll, clock=self.clock)
        self.cat.save({"x": 10, "y": 5, "mood": "curious", "asleep": False})
        self.archive = self.dir / "archive"
        self.archive.mkdir()
        (self.archive / "log-2026-10.md").write_text("SECRET-ARCHIVE-CONTENT", encoding="utf-8")
        self.p = VellumParser(self.cat, self.archive, rng=self.gift_roll, choose=lambda xs: xs[0])

    def say(self, line, at=(10, 6), who="owner", **kw):
        return self.p.respond(line, who, list(at), **kw)

    def mood(self, mood, asleep=False, **extra):
        self.cat.save({"x": 10, "y": 5, "mood": mood, "asleep": asleep, **extra})

    def make_fond(self):
        for _ in range(12):
            self.cat.tend("owner", "feed", [10, 6])
        self.assertEqual(tier(self.cat.affinity("owner"), WORDS), "fond")

    # ---------- reading the line ----------

    def test_synonyms_map_to_one_verb_and_object(self):
        for line, verb in (("> stroke kitty", "pet"), ("> boop it", "pet"), ("> x cat", "look"), ("> l", "look"),
                           ("> pspsps", "call"), ("> here kitty", "call"), ("> sniff", "smell"), ("> z", "wait"),
                           ("> ?", "help"), ("> hug vellum", "take"), ("> dangle string", "play"),
                           ("> offer sardine", "feed"), ("> hi", "talk")):
            self.assertEqual(self.p.parse(line)["verb"], verb, line)
        self.assertEqual(self.p.parse("> wave toy")["objects"], ["quill"])
        self.assertEqual(self.p.parse("> give treat to cat")["objects"], ["fish", "vellum"])

    def test_stop_words_are_dropped_and_no_object_means_vellum(self):
        p = self.p.parse("> Please GENTLY pet the cat on the belly")
        self.assertEqual((p["verb"], p["objects"], p["unknown"]), ("pet", ["vellum", "belly"], []))
        self.assertEqual(self.p.parse("> pet")["objects"], ["vellum"])
        self.assertEqual(self.p.parse("> pet it")["objects"], ["vellum"])
        self.assertEqual(self.p.parse(">look at a cat.")["objects"], ["vellum"])

    def test_out_of_range(self):
        res = self.say("> pet vellum", at=(13, 5))
        self.assertEqual(res["text"], WORDS["out_of_range"])
        self.assertFalse(res["ok"])
        self.assertEqual(self.say("> look", at=(12, 7))["text"], WORDS["look"]["curious"])  # two tiles is in range

    def test_pet_lands_only_within_one_tile(self):
        res = self.say("> pet", at=(12, 5))
        self.assertFalse(res["ok"])
        self.assertEqual(res["effect"], "none")
        self.assertEqual(self.cat.affinity("owner"), self.cat.affinity("tally"))  # nothing landed

    def test_unknown_verb_and_object(self):
        self.assertEqual(self.say("> juggle cat")["text"], "I don't know the word 'juggle'.")
        self.assertEqual(self.say("> pet dragon")["text"],
                         "You don't see a 'dragon' here. Vellum does, apparently, and is staring at it.")

    def test_failures_with_character(self):
        self.assertEqual(self.say("> open cat")["text"], "Vellum is not a door.")
        self.assertEqual(self.say("> eat fish")["text"], "That's Vellum's.")
        for v in ("kick", "hit", "throw", "push"):
            self.assertEqual(self.say(f"> {v} vellum")["text"], "No. Not in this vault.")
        self.assertEqual(self.say("> read cat")["text"], "Vellum is made of vellum only in name.")
        self.assertEqual(self.say("> xyzzy")["text"], "A hollow voice says 'Fool.' Vellum looks unimpressed.")

    # ---------- one state branch per verb ----------

    def test_look_by_state_and_part(self):
        self.mood("sleepy", asleep=True)
        self.assertEqual(self.say("> look")["text"], WORDS["look"]["sleepy"])
        self.mood("playful")
        self.assertEqual(self.say("> examine vellum")["text"], WORDS["look"]["playful"])
        self.assertEqual(self.say("> look tail")["text"], WORDS["look"]["tail"])
        self.assertEqual(self.say("> look paw")["text"], WORDS["look"]["paw"])

    def test_pet_by_state_with_the_existing_rules(self):
        self.mood("sleepy", asleep=True)
        res = self.say("> pet")
        self.assertEqual((res["text"], res["effect"]), (WORDS["pet"]["sleepy"], "blink"))
        self.mood("curious")
        res = self.say("> stroke vellum")
        self.assertEqual((res["text"], res["effect"]), (WORDS["pet"]["curious"], "purr"))
        self.assertGreater(self.cat.affinity("owner"), self.cat.affinity("tally"))  # an accepted pet raises it
        self.cat_roll.v = 0.01
        res = self.say("> pet")
        self.assertEqual((res["text"], res["effect"]), (WORDS["pet"]["walk_off"], "walk_off"))
        self.cat_roll.v = 0.9
        self.assertEqual(self.say("> pet belly")["text"], WORDS["pet"]["belly"])
        self.assertEqual(self.say("> scratch ears")["text"], WORDS["pet"]["ears"])

    def test_feed_only_when_hungry_and_it_resets_hunger(self):
        res = self.say("> feed cat")
        self.assertEqual((res["text"], res["effect"]), (WORDS["feed"]["other"], "none"))
        self.clock.t += HUNGRY_AFTER + 1
        self.assertTrue(self.cat.get()["hungry"])
        res = self.say("> give fish to vellum")
        self.assertEqual((res["text"], res["effect"], res["follow_seconds"]), (WORDS["feed"]["hungry"], "fed", 60))
        self.assertFalse(self.cat.get()["hungry"])
        self.assertEqual(self.say("> give quill to vellum")["text"], WORDS["feed"]["quill"])

    def test_play_by_state(self):
        self.assertEqual(self.say("> play")["text"], WORDS["play"]["curious"])
        self.mood("playful")
        res = self.say("> wave quill")
        self.assertEqual((res["text"], res["effect"]), (WORDS["play"]["playful"], "pounce"))
        self.mood("sleepy", asleep=True)
        self.assertEqual(self.say("> tease")["text"], WORDS["play"]["ignore"])

    def test_call_by_state_and_tier(self):
        self.mood("sleepy", asleep=True)
        self.assertEqual(self.say("> pspsps", at=(12, 5))["text"], WORDS["call"]["sleepy"])
        self.mood("curious")
        res = self.say("> call", at=(12, 5))
        self.assertEqual((res["text"], res["effect"]), (WORDS["call"]["awake"], "come"))
        self.make_fond()
        self.mood("curious")
        self.assertEqual(self.say("> here kitty", at=(12, 5))["text"], WORDS["call"]["awake_fond"])

    def test_talk_take_smell_listen_wait_help(self):
        self.assertEqual(self.say("> talk")["text"], WORDS["talk"]["default"])
        self.assertEqual(self.say("> ask about Tally")["text"], WORDS["talk"]["tally"])
        self.assertEqual(self.say("> tell secret")["text"], WORDS["talk"]["secret"])
        self.assertEqual(self.say("> pick up vellum")["text"], WORDS["take"]["not_fond"])
        self.assertEqual(self.say("> smell")["text"], WORDS["smell"])
        self.assertEqual(self.say("> listen")["text"], WORDS["listen"]["awake"])
        self.assertEqual(self.say("> wait")["text"], WORDS["wait"])
        self.assertEqual(self.say("> help")["text"], WORDS["help"])
        self.make_fond()
        self.assertEqual(self.say("> take cat")["text"], WORDS["take"]["fond"])

    # ---------- hidden responses ----------

    def test_gift_only_at_fond_and_only_the_title(self):
        self.gift_roll.v = 0.0  # the 1-in-20 roll always comes up
        res = self.say("> pet")
        self.assertNotIn("gift", res)  # wary: never
        self.make_fond()
        self.mood("curious")
        res = self.say("> pet")
        self.assertEqual(res["gift"], "log-2026-10")
        self.assertIn("'log-2026-10'", res["text"])
        self.assertNotIn("SECRET-ARCHIVE-CONTENT", json.dumps(res))
        self.gift_roll.v = WORDS["gift_odds"]  # just over the odds: no gift
        self.assertNotIn("gift", self.say("> pet"))

    def test_ledger_and_compass(self):
        self.mood("curious", on="on the desk, sitting squarely on the open ledger")
        self.cat_roll.v = 0.01  # would walk off, but it stays on the ledger
        res = self.say("> pet", waiting=2)
        self.assertEqual((res["text"], res["effect"]), (WORDS["hidden"]["ledger"], "purr"))
        self.mood("sleepy", asleep=True)
        self.assertEqual(self.say("> look", tile="compass_rose")["text"], WORDS["hidden"]["compass"])
        self.assertEqual(self.say("> look", tile="floor")["text"], WORDS["look"]["sleepy"])

    # ---------- guests ----------

    def test_guests_look_pet_call_talk_only_and_never_get_a_gift(self):
        self.gift_roll.v = 0.0
        for line in ("> look", "> pet", "> call", "> talk"):
            self.assertTrue(self.say(line, who="guest")["ok"], line)
        for line in ("> feed", "> play", "> take", "> smell"):
            self.assertFalse(self.say(line, who="guest")["ok"], line)
        self.make_fond()  # the owner's liking does not carry over to a guest
        res = self.say("> pet", who="guest")
        self.assertNotIn("gift", res)
        self.assertEqual(res["tier"], "wary")

    def test_words_come_from_the_data_file(self):
        f = self.dir / "words.json"
        f.write_text(json.dumps({**WORDS, "wait": "Edited by Tally."}), encoding="utf-8")
        p = VellumParser(self.cat, words_file=f)
        self.assertEqual(p.respond("> wait", "owner", [10, 6])["text"], "Edited by Tally.")


class DistanceTest(unittest.TestCase):
    def test_one_rule_for_next_to(self):
        self.assertEqual(steps({"x": 29, "y": 2}, {"x": 29, "y": 3}), 1)  # the 2026-11-25 report
        self.assertTrue(next_to([29, 2], {"x": 29, "y": 3}))
        self.assertTrue(next_to([1, 1], [2, 2]))  # a diagonal counts as next to
        self.assertFalse(next_to([1, 1], [3, 1]))
        self.assertIsNone(steps(None, [1, 1]))
        cat = Cat(Path(tempfile.mkdtemp()), rng=lambda: 0.9)
        cat.save({"x": 29, "y": 3, "mood": "curious"})
        self.assertIn("right next to you", cat.for_prompt({"x": 29, "y": 2}))
        self.assertTrue(cat.tend("tally", "pet", {"x": 29, "y": 2})["ok"])


class NeverAModelTest(unittest.TestCase):
    """A '>' line goes to the parser from every talk route, and no brain is ever asked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        vault = base / "vault"
        (vault / "archive").mkdir(parents=True)
        (vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        cfg = Config(core_dir=vault, state_dir=base / "state", claude_path="", config_path=base / "config.toml")
        self.game = Game(cfg)
        self.game.auth.set_password("supersecret")
        self.calls = []

        def no_model(*a, **k):
            self.calls.append(a)
            raise AssertionError("a model was asked")

        for brain in (self.game.brain, self.game.guest_brain):
            brain.talk = no_model
        self.game.brass_talk.reply = no_model
        self.game.cat.save({"x": 10, "y": 5, "mood": "curious"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.game))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, path, body, cookie):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def login(self, role, pw=""):
        req = urllib.request.Request(self.url + "/api/login", data=json.dumps({"role": role, "password": pw}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.headers.get("Set-Cookie", "").split(";")[0]

    def test_gt_lines_never_reach_a_model(self):
        owner = self.login("owner", "supersecret")
        cat = {"x": 10, "y": 5, "mood": "curious"}
        for path in ("/api/cat/parse", "/api/talk", "/api/brass/talk"):
            status, res = self.call(path, {"text": "> look at the cat", "at": [10, 6], "cat": cat}, owner)
            self.assertEqual(status, 200, (path, res))
            self.assertEqual(res["text"], WORDS["look"]["curious"], path)
            self.assertFalse(res["model"])
        status, res = self.call("/api/talk", {"text": "  > pet", "at": [30, 30], "cat": cat}, owner)
        self.assertEqual(res["text"], WORDS["out_of_range"])
        guest = self.login("guest")
        status, res = self.call("/api/talk", {"text": "> pet", "at": [10, 6], "cat": cat}, guest)
        self.assertEqual(status, 200, res)
        self.assertTrue(res["ok"])
        status, res = self.call("/api/cat/parse", {"text": "> feed", "at": [10, 6], "cat": cat}, guest)
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
