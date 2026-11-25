"""Tests for the ledger and the vault MCP server, against a small made-up vault in a temp folder.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.ledger import Ledger, LedgerError  # noqa: E402
from obsidian_vault import vault_mcp  # noqa: E402

SOUL = """# Soul

## For the coaching thread

Intro.

- First item.
- (nothing else open)

## Archive

Cold memory.

## Interaction log

- 2026-11-01 10:00 (Europe/Berlin), Project: Test · Theme: Games: first.

Related: [bootstrap.md](bootstrap.md)
"""


FAILURES = """# Agent failure log

## Entries

1. **2026-10-01.** First.
2. **2026-10-02.** Second.

Taxonomy note.

## How this log is used

1. **Record.** Same session.
"""


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / "vault"
        for d in ("games", "archive", "templates", ".obsidian", "Claude outputs"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "soul.md").write_text(SOUL, encoding="utf-8")
        (self.vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        (self.vault / "games" / "chess.md").write_text("# Chess\n\nOpening: e4.\n", encoding="utf-8")
        (self.vault / "archive" / "old.md").write_text("old\n", encoding="utf-8")
        (base / "outside.md").write_text("outside\n", encoding="utf-8")
        self.state = base / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, mode="review", soul_direct=False) -> Ledger:
        cfg = Config(core_dir=self.vault, writes_mode=mode, writes_folders=["games"], state_dir=self.state,
                     soul_direct=soul_direct)
        return Ledger(cfg)

    def soul(self) -> str:
        return (self.vault / "soul.md").read_text(encoding="utf-8")

    # ---------- modes ----------

    def test_off_writes_nothing(self):
        with self.assertRaises(LedgerError):
            self.ledger("off").append_log("Games", "hello")
        self.assertEqual(self.soul(), SOUL)

    def test_review_queues_then_approve_writes(self):
        led = self.ledger("review")
        r = led.append_log("Games", "Robin visited\nthe vault.", "games/chess.md")
        self.assertEqual(r.status, "queued")
        self.assertEqual(self.soul(), SOUL)
        [op] = led.pending()
        self.assertIn("+- ", op["diff"])
        led.approve(r.id)
        lines = self.soul().split("\n")
        i = lines.index("- 2026-11-01 10:00 (Europe/Berlin), Project: Test · Theme: Games: first.")
        new = lines[i + 1]
        self.assertIn("Project: The Obsidian Vault · Theme: Games ([note](games/chess.md)): Robin visited the vault.", new)
        self.assertEqual(lines[i + 2:], ["", "Related: [bootstrap.md](bootstrap.md)", ""])
        self.assertEqual(led.pending(), [])
        self.assertEqual(len(list((self.state / "backups").iterdir())), 1)
        journal = [json.loads(l) for l in (self.state / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(journal[-1]["event"], "written")

    def test_soul_is_tallys_own_in_review_mode(self):
        led = self.ledger("review", soul_direct=True)
        self.assertEqual(led.append_log("Games", "hi").status, "applied")
        r = led.edit_soul("- First item.", "- First item, revised.", "Sharper wording")
        self.assertEqual(r.status, "applied")
        self.assertIn("- First item, revised.", self.soul())
        with self.assertRaises(LedgerError):
            led.edit_soul("not in the soul", "x", "nothing")
        self.assertEqual(led.propose_edit("games/chess.md", "e4", "d4", "openings").status, "queued")

    def test_direct_writes_log_at_once_but_queues_notes(self):
        led = self.ledger("direct")
        self.assertEqual(led.append_log("Games", "hi").status, "applied")
        self.assertIn("Theme: Games: hi", self.soul())
        r = led.propose_edit("games/chess.md", "e4", "d4", "He switched openings")
        self.assertEqual(r.status, "queued")
        self.assertIn("e4", (self.vault / "games" / "chess.md").read_text())

    def test_coaching_goes_before_placeholder(self):
        led = self.ledger("direct")
        led.add_coaching("Ask about the game.")
        lines = self.soul().split("\n")
        i = lines.index("- (nothing else open)")
        self.assertTrue(lines[i - 1].startswith("- Ask about the game. (from The Obsidian Vault"))
        self.assertEqual(lines[i - 2], "- First item.")

    def test_failure_log_gets_the_next_number(self):
        (self.vault / "agent-failure-log.md").write_text(FAILURES, encoding="utf-8")
        led = self.ledger("review", soul_direct=True)
        r = led.log_failure("2026-11-25", "Said X.", "Caught by Robin.", "Check X.", "Unchecked-claim", "no (log only)")
        self.assertEqual(r.status, "applied")
        lines = (self.vault / "agent-failure-log.md").read_text(encoding="utf-8").split("\n")
        i = lines.index("2. **2026-10-02.** Second.")
        self.assertEqual(lines[i + 1], "3. **2026-11-25.** Said X. · Caught by Robin. · Check X. · "
                                       "**Category:** unchecked-claim · **Operative fix:** no (log only)")
        self.assertEqual(lines[i + 2:i + 4], ["", "Taxonomy note."])
        self.assertIn("1. **Record.** Same session.", lines)  # the numbered list under the next heading is untouched
        with self.assertRaises(LedgerError):
            led.log_failure("", "x", "y", "z", "vibes", "no")

    def test_failure_log_waits_for_review_when_soul_is_reviewed(self):
        (self.vault / "agent-failure-log.md").write_text(FAILURES, encoding="utf-8")
        led = self.ledger("review", soul_direct=False)
        r = led.log_failure("", "x", "y", "z", "process", "no")
        self.assertEqual(r.status, "queued")
        led.approve(r.id)
        self.assertIn("3. **", (self.vault / "agent-failure-log.md").read_text(encoding="utf-8"))

    def test_reject_writes_nothing(self):
        led = self.ledger()
        r = led.append_log("Games", "nope")
        led.reject(r.id)
        self.assertEqual(self.soul(), SOUL)
        self.assertEqual(led.pending(), [])

    # ---------- edits and notes ----------

    def test_edit_applies_only_if_unchanged(self):
        led = self.ledger()
        r = led.propose_edit("games/chess.md", "e4", "d4", "switch")
        (self.vault / "games" / "chess.md").write_text("# Chess\n\nOpening: e4. Edited elsewhere.\n")
        [op] = led.pending()
        self.assertTrue(op["stale"])
        with self.assertRaises(LedgerError):
            led.approve(r.id)
        self.assertIn("Edited elsewhere", (self.vault / "games" / "chess.md").read_text())

    def test_edit_needs_a_unique_passage(self):
        led = self.ledger()
        with self.assertRaises(LedgerError):
            led.propose_edit("games/chess.md", "zzz", "y", "r")
        with self.assertRaises(LedgerError):
            led.propose_edit("games/missing.md", "a", "b", "r")

    def test_new_note(self):
        led = self.ledger()
        r = led.propose_note("games/go.md", "---\ntags: [games]\n---\n# Go\n", "new theme note")
        led.approve(r.id)
        self.assertTrue((self.vault / "games" / "go.md").is_file())
        with self.assertRaises(LedgerError):
            led.propose_note("games/go.md", "again", "r")

    def test_forbidden_paths(self):
        led = self.ledger()
        for path in ["bootstrap.md", "archive/old.md", "templates/x.md", ".obsidian/app.md",
                     "../outside.md", "/etc/passwd", "games/x.txt", "soul.md", "Claude outputs/x.md",
                     "games/../bootstrap.md", "games/../../outside.md"]:
            with self.subTest(path=path):
                with self.assertRaises(LedgerError):
                    led.propose_note(path, "x", "r")
        with self.assertRaises(LedgerError):
            led.append_log("Games", "x", theme_link="../outside.md")
        self.assertFalse((self.state / "pending").exists() and any((self.state / "pending").iterdir()))

    def test_symlink_out_of_vault_is_refused(self):
        (self.vault / "games" / "link").symlink_to(Path(self.tmp.name))
        with self.assertRaises(LedgerError):
            self.ledger().propose_note("games/link/sneaky.md", "x", "r")

    def test_one_line_limits(self):
        led = self.ledger()
        with self.assertRaises(LedgerError):
            led.append_log("Games", "   ")
        with self.assertRaises(LedgerError):
            led.append_log("Games", "x" * 3000)

    def test_bad_ids_cannot_reach_other_files(self):
        with self.assertRaises(LedgerError):
            self.ledger().approve("../../journal")


class McpTest(unittest.TestCase):
    def test_protocol_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "soul.md").write_text(SOUL, encoding="utf-8")
            env = {"PYTHONPATH": str(ROOT), "TALLY_CORE_DIR": str(vault), "VAULT_WRITES": "direct",
                   "VAULT_STATE_DIR": str(Path(tmp) / "state"), "VAULT_CONFIG": str(Path(tmp) / "none.toml")}
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "append_interaction_log", "arguments": {"theme": "Games", "entry": "hello"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "propose_note", "arguments": {"path": "bootstrap.md", "content": "x", "reason": "r"}}},
            ]
            out = subprocess.run([sys.executable, "-m", "obsidian_vault.vault_mcp"], cwd=tmp, env=env, text=True,
                                 input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, timeout=20)
            replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
            self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "vault")
            self.assertEqual({t["name"] for t in replies[2]["result"]["tools"]}, {t["name"] for t in vault_mcp.TOOLS + vault_mcp.GAME_TOOLS})
            self.assertFalse(replies[3]["result"]["isError"])
            self.assertIn("Theme: Games: hello", (vault / "soul.md").read_text())
            self.assertTrue(replies[4]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
