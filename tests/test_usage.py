"""Tests for the usage meter: reading Claude Code's logs (a fixture, never real logs) and the block maths.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault import usage  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402

SECRET = "the words of the conversation, which the meter must never keep"


def ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def line(at: str, mid: str, rid: str = "r1", model: str = "claude-opus-5-5", session: str = "s1", **tokens) -> str:
    u = {"input_tokens": tokens.get("i", 0), "output_tokens": tokens.get("o", 0),
         "cache_read_input_tokens": tokens.get("r", 0), "cache_creation_input_tokens": tokens.get("w", 0)}
    return json.dumps({"type": "assistant", "timestamp": at + "Z", "sessionId": session, "requestId": rid,
                       "cwd": "~/somewhere", "message": {"id": mid, "model": model, "usage": u,
                                                          "content": [{"type": "text", "text": SECRET}]}})


def write_fixture(base: Path) -> Path:
    logs = base / "projects"
    (logs / "proj-a").mkdir(parents=True)
    (logs / "proj-b").mkdir(parents=True)
    a = [
        json.dumps({"type": "user", "timestamp": "2026-11-27T10:19:00Z", "message": {"content": SECRET}}),
        line("2026-11-27T10:20:00", "m1", i=1_000_000),            # $4.00
        line("2026-11-27T10:20:00", "m1", i=1_000_000),            # the same reply again: counted once
        "not json at all",
        line("2026-11-27T11:00:00", "m2", o=100_000),               # $2.00
    ]
    b = [
        line("2026-11-27T12:30:00", "m3", r=1_000_000, w=100_000, session="s2"),  # $0.20 + $0.80
        line("2026-11-26T22:00:00", "m0", o=50_000, session="s2"),               # yesterday: $1.00
        line("2026-11-27T12:31:00", "m4", model="<synthetic>", o=999_999, session="s2"),
    ]
    (logs / "proj-a" / "one.jsonl").write_text("\n".join(a) + "\n", encoding="utf-8")
    (logs / "proj-b" / "two.jsonl").write_text("\n".join(b) + "\n", encoding="utf-8")
    return logs


class PricingTest(unittest.TestCase):
    def test_opus_prices_from_the_report(self):
        self.assertAlmostEqual(usage.cost({"input": 1_000_000}, "claude-opus-5-5"), 4.00)
        self.assertAlmostEqual(usage.cost({"output": 1_000_000}, "claude-opus-5-5"), 20.00)
        self.assertAlmostEqual(usage.cost({"cache_read": 1_000_000}, "claude-opus-5-5"), 0.20)
        self.assertAlmostEqual(usage.cost({"cache_write": 1_000_000}, "claude-opus-5-5"), 8.00)  # 2x input

    def test_an_unknown_model_is_priced_high_not_low(self):
        self.assertEqual(usage.price_for("some-new-model"), usage.PRICES["opus"])


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = write_fixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_bookkeeping_and_counts_a_reply_once(self):
        entries = usage.read_entries(self.logs)
        self.assertEqual(len(entries), 4)  # m0..m3; the duplicate, the user line, junk and <synthetic> are out
        self.assertEqual([round(e["usd"], 2) for e in entries], [1.00, 4.00, 2.00, 1.00])
        self.assertNotIn(SECRET, json.dumps(entries))
        self.assertEqual(set(entries[0]), {"t", "model", "tokens", "usd", "session"})

    def test_since_and_session(self):
        self.assertEqual(len(usage.read_entries(self.logs, since=ts("2026-11-27T00:00:00"))), 3)
        s2 = usage.session_tokens(self.logs, "s2")
        self.assertEqual(s2["cache_read"], 1_000_000)
        self.assertAlmostEqual(s2["usd"], 2.00)
        self.assertIsNone(usage.session_tokens(self.logs, "nobody"))

    def test_a_missing_folder_reads_as_nothing(self):
        self.assertEqual(usage.read_entries(Path(self.tmp.name) / "nope"), [])


class BlockTest(unittest.TestCase):
    def e(self, at: str, usd: float = 1.0) -> dict:
        return {"t": ts(at), "usd": usd, "tokens": {"input": 10}}

    def test_a_block_starts_at_the_hour_and_lasts_five_hours(self):
        bs = usage.blocks([self.e("2026-11-27T10:20:00"), self.e("2026-11-27T11:00:00"),
                           self.e("2026-11-27T14:59:00"), self.e("2026-11-27T15:10:00")])
        self.assertEqual(len(bs), 2)
        self.assertEqual(bs[0]["start"], ts("2026-11-27T10:00:00"))
        self.assertEqual(bs[0]["end"], ts("2026-11-27T15:00:00"))
        self.assertAlmostEqual(bs[0]["usd"], 3.0)
        self.assertEqual(bs[1]["start"], ts("2026-11-27T15:00:00"))

    def test_a_gap_of_five_hours_starts_a_new_block(self):
        bs = usage.blocks([self.e("2026-11-27T01:20:00"), self.e("2026-11-27T06:30:00")])
        self.assertEqual([b["start"] for b in bs], [ts("2026-11-27T01:00:00"), ts("2026-11-27T06:00:00")])

    def test_current_block(self):
        entries = [self.e("2026-11-27T10:20:00")]
        self.assertIsNotNone(usage.current_block(entries, ts("2026-11-27T14:00:00")))
        self.assertIsNone(usage.current_block(entries, ts("2026-11-27T15:00:00")))  # it has reset
        self.assertIsNone(usage.current_block([], ts("2026-11-27T14:00:00")))


class MeterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = write_fixture(Path(self.tmp.name))
        self.cfg = Config(core_dir=None, state_dir=Path(self.tmp.name) / "state", usage_logs_dir=self.logs,
                          timezone="UTC")

    def tearDown(self):
        self.tmp.cleanup()

    def meter(self, at: str) -> usage.Meter:
        return usage.Meter(self.cfg, clock_fn=lambda: ts(at))

    def test_the_block_and_today(self):
        # the logs' mtimes are "now" in real time, so nothing is skipped by mtime here
        r = self.meter("2026-11-27T13:00:00").reading()
        self.assertEqual(r["block"]["start"], ts("2026-11-27T10:00:00"))
        self.assertEqual(r["block"]["reset"], ts("2026-11-27T15:00:00"))
        self.assertAlmostEqual(r["block_usd"], 7.00)  # 4 + 2 + 1
        self.assertAlmostEqual(r["today_usd"], 7.00)  # yesterday's $1 is not today's
        self.assertEqual(r["block"]["tokens"], 1_000_000 + 100_000 + 1_100_000)

    def test_the_line_for_game_state(self):
        self.cfg.usage_block_budget, self.cfg.usage_daily_cap = 14.0, 20.0
        line = self.meter("2026-11-27T13:00:00").line()
        self.assertEqual(line, "Usage: block 50% of budget ($7.00 of $14), resets 15:00; today $7.00 of $20. "
                               "This is an estimate from API prices, not the plan's accounting.")

    def test_room_for_a_job(self):
        m = self.meter("2026-11-27T13:00:00")
        self.cfg.usage_block_budget, self.cfg.usage_daily_cap = 10.0, 100.0
        self.assertEqual(m.room_for(2.5, m.reading()), (True, ""))
        ok, why = m.room_for(3.5, m.reading())
        self.assertFalse(ok)
        self.assertIn("block has $3.00 left of $10", why)
        self.cfg.usage_block_budget, self.cfg.usage_daily_cap = 100.0, 8.0
        ok, why = m.room_for(1.5, m.reading(fresh=True))
        self.assertFalse(ok)
        self.assertIn("today has $1.00 left of $8", why)

    def test_after_the_block_resets(self):
        r = self.meter("2026-11-27T18:00:00").reading()
        self.assertIsNone(r["block"])
        self.assertIn("no block open", self.meter("2026-11-27T18:00:00").line())

    def test_no_logs_means_unknown_and_no_room(self):
        self.cfg.usage_logs_dir = Path(self.tmp.name) / "missing"
        m = self.meter("2026-11-27T13:00:00")
        self.assertIn("unknown", m.line())
        self.assertFalse(m.room_for(0.01)[0])


class ResultTokensTest(unittest.TestCase):
    def test_per_model_totals_from_the_run(self):
        data = {"modelUsage": {"claude-opus-5-5": {"inputTokens": 100_000, "outputTokens": 50_000,
                                                    "cacheReadInputTokens": 2_000_000, "cacheCreationInputTokens": 100_000}}}
        tokens, model = usage.tokens_from_result(data)
        self.assertEqual(model, "claude-opus-5-5")
        self.assertAlmostEqual(tokens["usd"], 0.40 + 1.00 + 0.40 + 0.80)
        self.assertIsNone(usage.tokens_from_result({"result": "no counts here"}))


if __name__ == "__main__":
    unittest.main()
