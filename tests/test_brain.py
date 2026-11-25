"""Tests for the Claude Code brain's command line and the conversation surviving a restart.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.brains import ClaudeCodeBrain  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.persona import Persona, load_persona  # noqa: E402


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault, self.work = base / "vault", base / "work"
        self.vault.mkdir()
        self.work.mkdir()
        (self.vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (self.vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        self.state = base / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def brain(self, **kw) -> ClaudeCodeBrain:
        cfg = Config(core_dir=self.vault, state_dir=self.state, claude_path="/bin/true", **kw)
        return ClaudeCodeBrain(cfg, Persona("sys", ["soul.md"], []))

    def test_web_tools_and_work_dir(self):
        cmd = self.brain(work_dir=self.work)._command()
        tools = cmd[cmd.index("--tools") + 1].split(",")
        self.assertEqual(tools, ["Read", "Glob", "Grep", "WebSearch", "WebFetch"])
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(self.work))
        self.assertIn("mcp__vault__log_failure", cmd[cmd.index("--allowedTools") + 1])
        cmd = self.brain(web=False)._command()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("--add-dir", cmd)

    def test_session_survives_a_restart_and_reset_clears_it(self):
        b = self.brain()
        b.session_id = "abc"
        b._save_session()
        again = self.brain()
        self.assertEqual(again.session_id, "abc")
        self.assertEqual(again._command()[-2:], ["--resume", "abc"])
        again.reset()
        self.assertIsNone(self.brain().session_id)

    def test_persona_names_the_work_vault_only_when_set(self):
        cfg = Config(core_dir=self.vault, state_dir=self.state, work_dir=self.work)
        self.assertIn(f"The Work vault is at {self.work}", load_persona(cfg).system_prompt)
        cfg = Config(core_dir=self.vault, state_dir=self.state)
        self.assertIn("Work notes are not loaded here", load_persona(cfg).system_prompt)


if __name__ == "__main__":
    unittest.main()
