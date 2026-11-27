"""Tests for the errand queue: scheduling on approval, the scheduler's rules, cost recording and restarts.

A fake `claude` script stands in for Claude Code and a fake meter for the logs, so nothing real runs.
Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault import settings, vault_mcp  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.errands import DEFAULT_PROJECTION, REQUEUED, ErrandError, Errands  # noqa: E402
from obsidian_vault.ledger import Ledger, LedgerError  # noqa: E402
from obsidian_vault.scheduler import Scheduler, queue_status, queue_view  # noqa: E402
from obsidian_vault.tick import build_prompt  # noqa: E402

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
sys.stdin.read()
n = int(os.environ.get("FAKE_OUT", "100000"))
print(json.dumps({"result": "Done.", "total_cost_usd": 9.99, "num_turns": 2, "session_id": "s-fake",
                  "modelUsage": {"claude-opus-5-5": {"inputTokens": 0, "outputTokens": n,
                                                     "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}}}))
"""

BRIEF = "Read games/chess.md and report which opening it names, and nothing else."


class FakeMeter:
    """Stands in for usage.Meter: the block and the day are whatever the test says."""

    def __init__(self, block=0.0, today=0.0, budget=6.0, cap=12.0, readable=True):
        self.block, self.today, self.budget, self.cap, self.readable = block, today, budget, cap, readable

    def reading(self, fresh=False):
        return {"readable": self.readable, "block": {"reset": 0} if self.block else None,
                "block_usd": self.block, "today_usd": self.today, "block_budget": self.budget,
                "daily_cap": self.cap, "timezone": "UTC"}

    def line(self, r=None):
        return f"Usage: block ${self.block:.2f} of ${self.budget:g}; today ${self.today:.2f} of ${self.cap:g}."

    def room_for(self, projected, r=None):
        from obsidian_vault.usage import Meter
        return Meter.room_for(self, projected, r or self.reading())


class _Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / "vault"
        (self.vault / "games").mkdir(parents=True)
        (self.vault / "soul.md").write_text("# Soul\n", encoding="utf-8")
        (self.vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        self.claude = base / "claude"
        self.claude.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.claude.chmod(0o755)
        os.environ.pop("FAKE_OUT", None)
        self.cfg = Config(core_dir=self.vault, state_dir=base / "state", claude_path=str(self.claude),
                          writes_folders=["games"], writes_mode="review", usage_logs_dir=base / "no-logs",
                          timezone="UTC")
        self.errands = Errands(self.cfg)
        self.meter = FakeMeter()
        self.sched = Scheduler(self.cfg, self.errands, self.meter)
        self.ledger = Ledger(self.cfg, on_errand=self.errands.start, on_schedule=self.errands.schedule)

    def tearDown(self):
        os.environ.pop("FAKE_OUT", None)
        self.tmp.cleanup()

    def queue(self, title="Look at chess", kind="research") -> str:
        r = self.ledger.propose_errand(kind, title, BRIEF)
        self.assertIn("scheduled", self.ledger.approve(r.id, "room"))
        return r.id

    def wait(self, eid: str) -> dict:
        for _ in range(200):
            rec = self.errands.get(eid)
            if rec and rec["status"] not in ("running", "scheduled"):
                return rec
            time.sleep(0.05)
        self.fail("errand did not finish")


class ApproveTest(_Queue):
    def test_when_theres_room_schedules_instead_of_starting(self):
        eid = self.queue()
        self.assertEqual(self.ledger.pending(), [])
        rec = self.errands.get(eid)
        self.assertEqual(rec["status"], "scheduled")
        self.assertTrue(rec["scheduled"])
        self.assertEqual([r["id"] for r in self.errands.scheduled()], [eid])

    def test_run_now_is_unchanged(self):
        r = self.ledger.propose_errand("research", "Now", BRIEF)
        self.assertIn("Errand started", self.ledger.approve(r.id, "now"))
        self.assertEqual(self.wait(r.id)["status"], "done")

    def test_an_unknown_choice_is_refused_and_stays_pending(self):
        r = self.ledger.propose_errand("research", "Odd", BRIEF)
        with self.assertRaises(LedgerError):
            self.ledger.approve(r.id, "whenever")
        self.assertEqual(len(self.ledger.pending()), 1)


class SchedulerTest(_Queue):
    def test_never_starts_an_unapproved_errand(self):
        self.ledger.propose_errand("research", "Not approved", BRIEF)
        out = self.sched.check()
        self.assertIsNone(out["started"])
        self.assertEqual(self.errands.all(), [])  # nothing recorded, nothing ran
        self.assertEqual(len(self.ledger.pending()), 1)

    def test_starts_when_it_fits_and_records_the_cost(self):
        eid = self.queue()
        out = self.sched.check()
        self.assertEqual(out["started"], eid)
        rec = self.wait(eid)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["tokens"]["output"], 100_000)
        self.assertAlmostEqual(rec["est_usd"], 2.00)  # 100K output at $20 per million, not the run's own 9.99
        self.assertIn("Cost: about $2.00 (100K tokens), an estimate from API prices", self.errands.summary(rec))

    def test_waits_when_the_block_budget_has_no_room(self):
        eid = self.queue()
        self.meter.block, self.meter.budget = 5.0, 5.5  # $0.50 left; research is projected at $1.00

        out = self.sched.check()
        self.assertIsNone(out["started"])
        self.assertIn("block has $0.50 left of $5.5", out["reason"])
        self.assertEqual(self.errands.get(eid)["status"], "scheduled")
        view = queue_view(self.cfg, self.errands, self.meter)
        self.assertIn("block has", view["scheduled"][0]["waiting"])
        self.assertIn("Waiting for room, next check at", queue_status(self.cfg, self.errands, self.meter))

    def test_waits_when_the_daily_cap_has_no_room(self):
        eid = self.queue()
        self.meter.today = 11.5
        out = self.sched.check()
        self.assertIsNone(out["started"])
        self.assertIn("today has $0.50 left of $12", out["reason"])
        self.assertEqual(self.errands.get(eid)["status"], "scheduled")

    def test_waits_when_usage_cannot_be_read(self):
        self.queue()
        self.meter.readable = False
        self.assertIn("cannot be read", self.sched.check()["reason"])

    def test_one_at_a_time(self):
        first, second = self.queue("First"), self.queue("Second")
        running = self.errands.get(first)
        running["status"] = "running"  # as if it were under way
        self.errands._save(running)
        out = self.sched.check()
        self.assertIsNone(out["started"])
        self.assertIn("one at a time", out["reason"])
        self.assertEqual(self.errands.get(second)["status"], "scheduled")
        with self.assertRaises(ErrandError):
            self.errands.start_scheduled(second)

    def test_oldest_first_unless_moved(self):
        a = self.queue("A")
        time.sleep(0.01)
        b = self.queue("B")
        time.sleep(0.01)
        c = self.queue("C")
        self.assertEqual([r["id"] for r in self.errands.scheduled()], [a, b, c])
        self.errands.move(c, "up")
        self.errands.move(c, "up")
        self.assertEqual([r["id"] for r in self.errands.scheduled()], [c, a, b])
        self.errands.move(a, "down")
        self.assertEqual([r["id"] for r in self.errands.scheduled()], [c, b, a])
        self.assertEqual(self.sched.check()["started"], c)

    def test_only_the_head_of_the_queue_is_tried(self):
        big = self.queue("Big code job", kind="code")  # projected at the code guess, $2.60
        small = self.queue("Small research")            # $1.00 would fit, but it is not first
        self.meter.block, self.meter.budget = 4.0, 6.0  # $2 left
        self.assertIsNone(self.sched.check()["started"])
        self.assertEqual(self.errands.get(small)["status"], "scheduled")
        self.assertEqual(self.errands.get(big)["status"], "scheduled")


class ProjectionTest(_Queue):
    def test_starting_guess_then_running_average(self):
        self.assertEqual(self.errands.projection("research"), (DEFAULT_PROJECTION["research"], 0))
        for n in ("100000", "200000"):
            os.environ["FAKE_OUT"] = n
            r = self.ledger.propose_errand("research", f"Run {n}", BRIEF)
            self.ledger.approve(r.id, "now")
            self.wait(r.id)
        est, runs = self.errands.projection("research")
        self.assertEqual(runs, 2)
        self.assertAlmostEqual(est, 3.00)  # ($2 + $4) / 2
        self.assertEqual(self.errands.projection("code"), (DEFAULT_PROJECTION["code"], 0))


class RestartTest(_Queue):
    def running(self, eid: str) -> None:
        rec = self.errands.get(eid)
        rec["status"] = "running"
        self.errands._save(rec)

    def test_a_scheduled_errand_goes_back_to_the_queue(self):
        eid = self.queue()
        self.running(eid)
        self.assertEqual(self.errands.close_interrupted(), ["Look at chess"])
        rec = self.errands.get(eid)
        self.assertEqual(rec["status"], "scheduled")
        self.assertEqual(rec["interrupted"], 1)
        told = self.errands.unreported()
        self.assertEqual([r["id"] for r in told], [eid])  # Tally hears of it, once
        self.assertIn(REQUEUED, self.errands.summary(told[0]))
        self.assertEqual(self.errands.unreported(), [])
        # and the next check starts it again, telling the rerun it was cut off
        self.assertEqual(self.sched.check()["started"], eid)
        self.assertIn("cut off by a restart", self.errands.system_prompt(self.errands.get(eid)))
        self.assertEqual(self.wait(eid)["status"], "done")

    def test_a_run_now_errand_is_still_marked_failed(self):
        r = self.ledger.propose_errand("research", "Now", BRIEF)
        self.ledger.approve(r.id, "now")
        self.wait(r.id)
        self.running(r.id)
        self.errands.close_interrupted()
        self.assertEqual(self.errands.get(r.id)["status"], "failed")


class ReadOnlyTest(_Queue):
    def test_queue_status_tool_is_read_only(self):
        eid = self.queue()
        names = [t["name"] for t in vault_mcp.handle(self.ledger, {"id": 1, "method": "tools/list"})["result"]["tools"]]
        self.assertIn("queue_status", names)
        before = json.dumps(self.errands.all(), sort_keys=True)
        reply = vault_mcp.handle(self.ledger, {"id": 2, "method": "tools/call",
                                               "params": {"name": "queue_status", "arguments": {}}})
        text = reply["result"]["content"][0]["text"]
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("Look at chess", text)
        self.assertIn("Only OWNER approves, reorders or starts them", text)
        self.assertEqual(json.dumps(self.errands.all(), sort_keys=True), before)
        self.assertEqual(self.errands.get(eid)["status"], "scheduled")

    def test_the_tick_hears_the_queue_but_cannot_act_on_it(self):
        world = {"rooms": [], "spots": [], "tally": [0, 0], "spawn": [0, 0]}
        prompt = build_prompt(world, {"queue": "Usage: block 10% of budget."})
        self.assertIn("Usage: block 10% of budget.", prompt)
        self.assertIn("cannot start, stop or reorder errands", prompt)


class SettingsTest(unittest.TestCase):
    def test_budget_and_cap_go_to_the_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[tally]\ncore_dir = \"\"\n", encoding="utf-8")
            cfg = Config(core_dir=None, config_path=path)
            res = settings.save(cfg, {"block_budget": "7.5", "daily_cap": 15})
            self.assertEqual(sorted(res["changed"]), ["block_budget", "daily_cap"])
            self.assertIn("[usage]\nblock_budget = 7.5\ndaily_cap = 15.0", path.read_text(encoding="utf-8"))
            self.assertEqual((cfg.usage_block_budget, cfg.usage_daily_cap), (7.5, 15.0))
            self.assertEqual(settings.view(cfg)["block_budget"], 7.5)
            with self.assertRaises(settings.SettingsError):
                settings.save(cfg, {"daily_cap": "lots"})


if __name__ == "__main__":
    unittest.main()
