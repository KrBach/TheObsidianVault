"""Tests for [names]: the code never names a person; the configured names go in at the edges.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.config import load_config  # noqa: E402
from obsidian_vault.names import clean_name, fill  # noqa: E402
from obsidian_vault.persona import load_guest_persona, load_persona  # noqa: E402
from obsidian_vault.server import Game  # noqa: E402


class NamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        vault = base / "vault"
        vault.mkdir()
        (vault / "soul.md").write_text("# Soul\nI am Tally, and OWNER is a word I keep as written.\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        self.conf = base / "config.toml"
        self.conf.write_text(
            f'[names]\nowner = "Robin"\ncompanion = "Quill"\nowner_portrait = "portraits/robin.jpg"\n'
            f'[tally]\ncore_dir = "{vault}"\ntimezone = "Europe/Berlin"\n'
            f'[writes]\nstate_dir = "{base / "state"}"\n', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fill(self):
        self.assertEqual(fill("OWNER's desk; Tally waits for OWNER.", "Robin", "Quill"), "Robin's desk; Quill waits for Robin.")
        self.assertEqual(fill("Tallyho, OWNERS", "Robin", "Quill"), "Tallyho, OWNERS")  # whole words only
        self.assertEqual(clean_name('  Ro  bin ', "x"), "Ro bin")
        self.assertEqual(clean_name('<script>', "Owner"), "Owner")

    def test_config_and_prompts_use_the_names(self):
        cfg = load_config(self.conf)
        self.assertEqual((cfg.owner, cfg.companion, cfg.timezone), ("Robin", "Quill", "Europe/Berlin"))
        prompt = load_persona(cfg).system_prompt
        self.assertIn("Robin", prompt)
        self.assertNotIn("OWNER's vault", prompt)
        self.assertIn("I am Tally, and OWNER is a word I keep as written.", prompt)  # the core is left as written
        guest = load_guest_persona(cfg).system_prompt
        self.assertIn("Quill", guest)
        self.assertNotRegex(guest, r"\bOWNER\b|\bTally\b")
        world = Game(cfg).world()
        self.assertEqual(world["names"]["owner"], "Robin")
        self.assertEqual(world["creatures"]["player"]["name"], "Robin")
        self.assertEqual(world["creatures"]["player"]["portrait"]["image"], "portraits/robin.jpg")
        self.assertEqual(world["creatures"]["tally"]["name"], "Quill")


if __name__ == "__main__":
    unittest.main()
