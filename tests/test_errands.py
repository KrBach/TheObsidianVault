"""Tests for errands (queued in the ledger, run by a separate Claude Code session) and the notice board.

A fake `claude` script stands in for Claude Code, so nothing real runs.
Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.board import Board, BoardError  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.errands import COST_TOOLS, Errands  # noqa: E402
from obsidian_vault.ledger import Ledger, LedgerError  # noqa: E402

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
with open(os.environ["FAKE_ARGS"], "w") as f:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, f)
if os.environ.get("FAKE_FAIL"):
    print(json.dumps({"is_error": True, "result": "it broke"}))
    sys.exit(1)
print(json.dumps({"result": "Did the thing. **Checked** it.", "total_cost_usd": 0.12, "num_turns": 3}))
"""

BRIEF = "Read games/chess.md and report which opening it names, and nothing else."


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / "vault"
        (self.vault / "games").mkdir(parents=True)
        (self.vault / "Claude outputs" / "sub").mkdir(parents=True)
        (self.vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (self.vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        (self.vault / "games" / "chess.md").write_text("# Chess\n", encoding="utf-8")
        (self.vault / "Claude outputs" / "review.md").write_text("# Review\n", encoding="utf-8")
        (self.vault / "Claude outputs" / "sub" / "dash.html").write_text("<p>hi</p>", encoding="utf-8")
        (self.vault / "Claude outputs" / "secret.py").write_text("x = 1\n", encoding="utf-8")
        self.claude = base / "claude"
        self.claude.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.claude.chmod(0o755)
        self.args = base / "args.json"
        os.environ["FAKE_ARGS"] = str(self.args)
        os.environ.pop("FAKE_FAIL", None)
        self.base = base

    def tearDown(self):
        os.environ.pop("FAKE_FAIL", None)
        self.tmp.cleanup()

    def cfg(self, **kw) -> Config:
        kw.setdefault("writes_mode", "review")
        return Config(core_dir=self.vault, state_dir=self.base / "state", claude_path=str(self.claude),
                      writes_folders=["games"], **kw)

    def wait(self, errands: Errands, eid: str) -> dict:
        for _ in range(100):
            rec = errands.get(eid)
            if rec and rec["status"] != "running":
                return rec
            time.sleep(0.05)
        self.fail("errand did not finish")



class ErrandTest(_Vault):
    def test_errand_waits_for_approval_then_runs_and_reports_once(self):
        cfg = self.cfg()
        errands = Errands(cfg)
        led = Ledger(cfg, on_errand=errands.start)
        r = led.propose_errand("research", "Chess opening", BRIEF)
        self.assertEqual(r.status, "queued")
        self.assertFalse(self.args.exists())  # nothing ran yet
        [op] = led.pending()
        self.assertIn(BRIEF, op["diff"])
        self.assertIn("Errand started", led.approve(r.id))
        rec = self.wait(errands, r.id)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["cost_usd"], 0.12)
        ran = json.loads(self.args.read_text())
        self.assertEqual(Path(ran["cwd"]).resolve(), self.vault.resolve())
        self.assertIn(BRIEF, ran["prompt"])
        argv = ran["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep,WebSearch,WebFetch")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("# Errand", argv[argv.index("--append-system-prompt") + 1])
        self.assertEqual(led.pending(), [])
        [told] = errands.unreported()
        self.assertIn("Did the thing", errands.summary(told))
        self.assertEqual(errands.unreported(), [])

    def test_mcp_side_cannot_start_errands(self):
        led = Ledger(self.cfg())  # the MCP server's ledger has no runner
        r = led.propose_errand("research", "x", BRIEF)
        with self.assertRaises(LedgerError):
            led.approve(r.id)
        self.assertEqual(len(led.pending()), 1)

    def test_bad_errands_are_refused(self):
        led = Ledger(self.cfg())
        with self.assertRaises(LedgerError):
            led.propose_errand("anything", "x", BRIEF)
        with self.assertRaises(LedgerError):
            led.propose_errand("research", "x", "too short")
        with self.assertRaises(LedgerError):
            Ledger(self.cfg(writes_mode="off")).propose_errand("research", "x", BRIEF)
        cfg = self.cfg()
        cfg.errands = False
        with self.assertRaises(LedgerError):
            Ledger(cfg).propose_errand("research", "x", BRIEF)

    def test_failed_errand_is_reported_as_failed(self):
        os.environ["FAKE_FAIL"] = "1"
        cfg = self.cfg()
        errands = Errands(cfg)
        led = Ledger(cfg, on_errand=errands.start)
        r = led.propose_errand("research", "x", BRIEF)
        led.approve(r.id)
        rec = self.wait(errands, r.id)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("it broke", rec["result"])

    def test_session_blocks_credit_tools_unless_a_limit_was_approved(self):
        errands = Errands(self.cfg())
        rec = {"id": "a1", "kind": "session", "title": "t", "brief": BRIEF, "spend_usd": 0}
        cmd = errands.command(rec)
        self.assertNotIn("--strict-mcp-config", cmd)  # his own connectors and skills
        self.assertIn("mcp__Spriteforge__generate", cmd[cmd.index("--disallowedTools") + 1])
        self.assertIn("mcp__recraft__generate_image", COST_TOOLS)
        self.assertIn("blocked", cmd[cmd.index("--append-system-prompt") + 1])
        cmd = errands.command({**rec, "spend_usd": 2.5})
        self.assertNotIn("--disallowedTools", cmd)
        self.assertIn("$2.50", cmd[cmd.index("--append-system-prompt") + 1])

    def test_code_errand_works_in_its_own_worktree_and_branch(self):
        repo = self.base / "repo"
        repo.mkdir()
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (repo / "README.md").write_text("hi\n")
        git("add", ".")
        git("commit", "-qm", "init")
        cfg = self.cfg()
        cfg.errand_repo, cfg.errand_worktrees = repo, self.base / "worktrees"
        errands = Errands(cfg)
        led = Ledger(cfg, on_errand=errands.start)
        r = led.propose_errand("code", "Tweak", BRIEF)
        self.assertIn(f"errand/{r.id}", led.approve(r.id))
        rec = self.wait(errands, r.id)
        ran = json.loads(self.args.read_text())
        self.assertEqual(Path(ran["cwd"]).resolve(), (self.base / "worktrees" / r.id).resolve())
        allowed = ran["argv"][ran["argv"].index("--allowedTools") + 1]
        self.assertIn(f"Bash(git push -u origin errand/{r.id})", allowed)
        self.assertNotIn("Bash(git push:*)", allowed)
        self.assertEqual(rec["commits"], [])
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                                        capture_output=True, text=True).stdout.strip(), "main")


class BoardTest(_Vault):
    def test_files_are_listed_and_served_only_from_board_folders(self):
        cfg = self.cfg()
        board = Board(cfg)
        files = {f["where"]: f for f in board.files()}
        self.assertEqual(set(files), {"Claude outputs/review.md", "Claude outputs/sub/dash.html"})
        body, ctype = board.open_file(files["Claude outputs/sub/dash.html"]["id"])
        self.assertEqual(body, b"<p>hi</p>")
        self.assertTrue(ctype.startswith("text/html"))
        with self.assertRaises(BoardError):
            board.open_file("f../../soul.md")

    def test_pins(self):
        board = Board(self.cfg())
        board.pin("Arknights dashboard", "https://claude.ai/artifact/abc", "v92")
        board.pin("Arknights dashboard v2", "https://claude.ai/artifact/abc")  # same url: replaced, not doubled
        self.assertEqual([p["title"] for p in board.pins()], ["Arknights dashboard v2"])
        with self.assertRaises(BoardError):
            board.pin("x", "javascript:alert(1)")
        board.unpin("https://claude.ai/artifact/abc")
        self.assertEqual(board.pins(), [])
        kinds = {i["kind"] for i in board.listing([{"id": "e1", "kind": "research", "title": "t", "status": "done"}])}
        self.assertEqual(kinds, {"report", "note", "page"})


if __name__ == "__main__":
    unittest.main()
