"""Tests for dashboards: pages the game builds from a vault JSON file and hands to the Artifact tool.

A fake `claude` script stands in for Claude Code, so nothing real runs or gets published.
Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault import dashboards  # noqa: E402
from obsidian_vault.config import Config, load_config  # noqa: E402
from obsidian_vault.errands import Errands  # noqa: E402

# Plays both runs: the errand (it changes the data file, as a data check would) and the publish run.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
log = os.environ["FAKE_LOG"]
calls = json.load(open(log)) if os.path.exists(log) else []
calls.append({"argv": sys.argv[1:], "prompt": prompt})
json.dump(calls, open(log, "w"))
usage = {"m": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 0}}
if prompt.startswith("Errand:"):
    if os.environ.get("FAKE_TOUCH"):
        with open(os.environ["FAKE_TOUCH"], "w") as f:
            json.dump({"snapshots": [{"t": "27 Nov", "x": "</script><b>"}]}, f)
    print(json.dumps({"result": "Data check done.", "modelUsage": usage}))
else:
    print(json.dumps({"result": os.environ.get("FAKE_PUBLISH", "PUBLISHED https://claude.ai/artifact/abc"),
                      "modelUsage": usage}))
"""

TEMPLATE = "<title>Depot</title>\n<script>\nconst DATA=__DASHBOARD_DATA__;\ndocument.title=DATA.snapshots.length;\n</script>\n"
URL = "https://claude.ai/artifact/abc"


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.vault = base / "vault"
        (self.vault / "games").mkdir(parents=True)
        (self.vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        (self.vault / "soul.md").write_text("# Soul\n" + "x" * 1000, encoding="utf-8")
        self.data = self.vault / "games" / "history.json"
        self.data.write_text(json.dumps({"snapshots": [{"t": "24 Nov"}]}), encoding="utf-8")
        (self.vault / "games" / "dash.template.html").write_text(TEMPLATE, encoding="utf-8")
        self.claude = base / "claude"
        self.claude.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.claude.chmod(0o755)
        self.log = base / "calls.json"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ.pop("FAKE_TOUCH", None)
        os.environ.pop("FAKE_PUBLISH", None)

    def tearDown(self):
        for k in ("FAKE_LOG", "FAKE_TOUCH", "FAKE_PUBLISH"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def cfg(self, artifact=URL, **kw) -> Config:
        cfg = Config(core_dir=self.vault, state_dir=self.base / "state", claude_path=str(self.claude), **kw)
        cfg.errand_browser = "off"
        cfg.dashboards = {"depot": {"data": "games/history.json", "template": "games/dash.template.html",
                                    "artifact": artifact}}
        return cfg

    def calls(self) -> list:
        return json.loads(self.log.read_text()) if self.log.exists() else []

    def wait(self, errands: Errands, eid: str) -> dict:
        for _ in range(200):
            rec = errands.get(eid)
            if rec and rec["status"] != "running":
                return rec
            time.sleep(0.05)
        self.fail("errand did not finish")

    def run_errand(self, cfg, kind="session") -> dict:
        errands = Errands(cfg)
        errands.start({"id": "e1", "errand_kind": kind, "title": "Data check", "brief": "Do the data check."})
        return self.wait(errands, "e1")

    # ---------- building ----------

    def test_render_puts_the_json_in_and_cannot_close_the_script(self):
        page = dashboards.render(TEMPLATE, json.dumps({"a": "</script><b>x"}))
        self.assertNotIn("__DASHBOARD_DATA__", page)
        self.assertEqual(page.count("</script>"), 1)  # only the template's own
        self.assertIn('{"a":"<\\/script><b>x"}', page)
        with self.assertRaises(dashboards.DashboardError):
            dashboards.render("<p>no placeholder</p>", "{}")
        with self.assertRaises(dashboards.DashboardError):
            dashboards.render(TEMPLATE, "{not json")

    def test_build_writes_the_page_to_claude_outputs_by_default(self):
        [d] = dashboards.configured(self.cfg())
        self.assertEqual(d.output, (self.vault / "Claude outputs" / "depot.html").resolve())
        built = dashboards.build(d)
        self.assertIn('"24 Nov"', d.output.read_text(encoding="utf-8"))
        self.assertGreater(built["bytes"], len(TEMPLATE) - 30)

    def test_paths_outside_the_vault_are_refused(self):
        cfg = self.cfg()
        cfg.dashboards["bad"] = {"data": "../elsewhere.json", "template": "games/dash.template.html"}
        self.assertEqual([d.name for d in dashboards.configured(cfg)], ["depot"])

    def test_config_reads_dashboards_and_per_kind_models(self):
        path = self.base / "config.toml"
        path.write_text(
            '[tally]\ncore_dir = "%s"\n\n[errands]\nsession_model = "sonnet"\ncore_files = ["bootstrap.md"]\n\n'
            '[dashboards]\npublish = "off"\n\n[dashboards.depot]\ndata = "games/history.json"\n'
            'template = "games/dash.template.html"\nartifact = "%s"\n\n[dashboards.half]\ndata = "x.json"\n'
            % (self.vault, URL), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            cfg = load_config(path)
        self.assertIn("dashboards.half", err.getvalue())
        self.assertEqual(cfg.dashboard_publish, "off")
        self.assertEqual(list(cfg.dashboards), ["depot"])  # "half" has no template
        self.assertEqual(cfg.dashboards["depot"]["artifact"], URL)
        self.assertEqual(cfg.errand_kind_models, {"session": "sonnet"})
        self.assertEqual(cfg.errand_core_files, ["bootstrap.md"])

    # ---------- after an errand ----------

    def test_errand_that_changes_the_data_gets_the_page_built_and_published(self):
        os.environ["FAKE_TOUCH"] = str(self.data)
        rec = self.run_errand(self.cfg())
        self.assertEqual(rec["status"], "done")
        page = (self.vault / "Claude outputs" / "depot.html").read_text(encoding="utf-8")
        self.assertIn('"27 Nov"', page)
        self.assertIn("published to " + URL, rec["result"])
        self.assertTrue(rec["result"].startswith("Data check done."))
        errand_call, publish_call = self.calls()
        argv = publish_call["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Artifact")  # nothing else, no MCP servers
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertIn(URL, publish_call["prompt"])
        self.assertIn(str((self.vault / "Claude outputs" / "depot.html").resolve()), publish_call["prompt"])
        self.assertNotIn("27 Nov", publish_call["prompt"])  # the publish run never sees the data
        # The errand was told the game does the page, and to keep the run lean.
        system = errand_call["argv"][errand_call["argv"].index("--append-system-prompt") + 1]
        self.assertIn("The game itself builds and publishes", system)
        self.assertIn("games/history.json", system)
        self.assertIn("python3 script", system)
        # The publish run's tokens count toward the errand's cost.
        self.assertEqual(rec["tokens"]["cache_read"], 2000)

    def test_a_publish_that_fails_says_so_and_keeps_the_built_page(self):
        os.environ["FAKE_TOUCH"] = str(self.data)
        os.environ["FAKE_PUBLISH"] = "NOT PUBLISHED: no Artifact tool in this run"
        rec = self.run_errand(self.cfg())
        self.assertEqual(rec["status"], "done")  # the data check itself went fine
        self.assertIn("NOT published", rec["result"])
        self.assertIn("no Artifact tool in this run", rec["result"])
        self.assertTrue((self.vault / "Claude outputs" / "depot.html").is_file())

    def test_unchanged_data_builds_nothing(self):
        old = time.time() - 3600
        os.utime(self.data, (old, old))
        rec = self.run_errand(self.cfg())
        self.assertNotIn("Dashboards", rec["result"])
        self.assertFalse((self.vault / "Claude outputs" / "depot.html").exists())
        self.assertEqual(len(self.calls()), 1)  # no publish run

    def test_publishing_off_builds_only(self):
        os.environ["FAKE_TOUCH"] = str(self.data)
        cfg = self.cfg()
        cfg.dashboard_publish = "off"
        rec = self.run_errand(cfg)
        self.assertIn("Publishing is off", rec["result"])
        self.assertEqual(len(self.calls()), 1)

    # ---------- cost knobs ----------

    def test_model_per_kind_and_lighter_core(self):
        cfg = self.cfg(errand_model="opus")
        cfg.errand_kind_models = {"session": "sonnet"}
        cfg.errand_core_files = ["bootstrap.md"]
        errands = Errands(cfg)
        rec = {"id": "x", "kind": "session", "title": "t", "brief": "b", "spend_usd": 0}
        cmd = errands.command(rec)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        system = cmd[cmd.index("--append-system-prompt") + 1]
        self.assertIn('name="bootstrap.md"', system)
        self.assertNotIn('name="soul.md"', system)
        rec["kind"] = "research"
        cmd = errands.command(rec)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")


if __name__ == "__main__":
    unittest.main()
