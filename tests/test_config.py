"""Tests for the local config: where it lives, first run, the one-time migration, and the Settings window.

Everything runs in temp folders; the real per-user config is never read or written.
Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obsidian_vault import config as config_mod, minitoml  # noqa: E402
from obsidian_vault.config import Config, load_config  # noqa: E402
from obsidian_vault.server import Game, make_handler  # noqa: E402


class TomlEditTest(unittest.TestCase):
    def test_set_values_keeps_comments_and_other_lines(self):
        text = '# top\n[names]\n# who\nowner = "Your name"  # you\ncompanion = "Tally"\n\n[tally]\ncore_dir = ""\n'
        out = minitoml.set_values(text, "names", {"owner": 'Ro "bin"'})
        self.assertIn("# who\n", out)
        self.assertIn('companion = "Tally"', out)
        self.assertEqual(minitoml.loads(out)["names"]["owner"], 'Ro "bin"')
        out = minitoml.set_values(out, "tally", {"work_dir": "~/W", "core_dir": "~/V"})
        data = minitoml.loads(out)
        self.assertEqual((data["tally"]["core_dir"], data["tally"]["work_dir"]), ("~/V", "~/W"))
        out = minitoml.set_values(out, "errands", {"browser": "off"})
        self.assertEqual(minitoml.loads(out)["errands"]["browser"], "off")
        self.assertEqual(minitoml._loads_minimal(out), minitoml.loads(out))  # the fallback reader agrees

    def test_short_path_hides_the_middle(self):
        home = Path.home()
        self.assertEqual(config_mod.short_path(home / "a" / "secret-folder" / "Private"), "~/…/Private")
        self.assertEqual(config_mod.short_path(home / "Vault"), "~/Vault")
        self.assertEqual(config_mod.short_path("/opt/x/y"), "/…/y")
        self.assertEqual(config_mod.home_form(home / "V" / "P"), "~/V/P")


class LocalConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.local = self.base / "home" / "config.toml"
        self.legacy = self.base / "repo" / "config.toml"
        self.legacy.parent.mkdir()
        self.state = self.base / "state"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VAULT_", "TALLY_"))}
        self.patches = [mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(config_mod, "LEGACY_CONFIG", self.legacy),
                        mock.patch.object(config_mod, "user_config_dir", lambda: self.local.parent)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_default_location_is_outside_the_repo(self):
        self.patches[2].stop()  # the real function, only to look at the path it gives
        try:
            d = config_mod.user_config_dir()
        finally:
            self.patches[2].start()
        self.assertEqual(d.name, "TheObsidianVault")
        self.assertNotIn(ROOT.resolve(), d.resolve().parents)

    def test_first_run_creates_it_from_the_example_without_placeholders(self):
        cfg = load_config()
        self.assertTrue(self.local.is_file())
        self.assertEqual(cfg.config_path, self.local.resolve())
        data = minitoml.loads(self.local.read_text(encoding="utf-8"))
        self.assertEqual((data["names"]["owner"], data["tally"]["core_dir"]), ("", ""))
        self.assertIsNone(cfg.core_dir)
        self.assertEqual(cfg.owner, "Owner")
        self.assertIn("# Time zone", self.local.read_text(encoding="utf-8"))  # the comments came along

    def test_old_config_and_settings_move_in_once(self):
        vault = self.base / "vault"
        vault.mkdir()
        self.legacy.write_text(f'# mine\n[names]\nowner = "Robin"\n[tally]\ncore_dir = "{vault}"\n'
                               f'[writes]\nstate_dir = "{self.state}"\n', encoding="utf-8")
        self.state.mkdir()
        (self.state / "settings.json").write_text(json.dumps(
            {"owner": "Robin B", "companion": "Quill", "timezone": "Europe/Berlin", "portrait": True}), encoding="utf-8")
        cfg = load_config()
        self.assertEqual((cfg.owner, cfg.companion, cfg.timezone, cfg.core_dir), ("Robin B", "Quill", "Europe/Berlin", vault))
        self.assertFalse(self.legacy.exists())
        self.assertTrue(self.legacy.with_name("config.toml.migrated").is_file())  # renamed, not deleted
        self.assertFalse((self.state / "settings.json").exists())
        self.assertTrue((self.state / "settings.json.migrated").is_file())
        self.assertIn("# mine", self.local.read_text(encoding="utf-8"))
        # an update brings a new config.toml-less game folder: the local file is left as it is
        before = self.local.read_text(encoding="utf-8")
        again = load_config()
        self.assertEqual(self.local.read_text(encoding="utf-8"), before)
        self.assertEqual(again.owner, "Robin B")

    def test_an_explicit_path_never_migrates(self):
        self.state.mkdir()
        (self.state / "settings.json").write_text('{"owner": "Robin"}', encoding="utf-8")
        p = self.base / "c.toml"
        p.write_text(f'[writes]\nstate_dir = "{self.state}"\n', encoding="utf-8")
        cfg = load_config(p)
        self.assertEqual(cfg.owner, "Owner")
        self.assertTrue((self.state / "settings.json").exists())
        self.assertFalse(self.local.exists())

    def test_browser_setting(self):
        p = self.base / "c.toml"
        p.write_text('[errands]\nbrowser = "Chrome"\n', encoding="utf-8")
        self.assertEqual(load_config(p).errand_browser, "chrome")
        p.write_text('[errands]\nbrowser = "netscape"\n', encoding="utf-8")
        self.assertEqual(load_config(p).errand_browser, "off")
        p.write_text("", encoding="utf-8")
        self.assertEqual(load_config(p).errand_browser, "safari")


class SettingsWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault, self.vault2, self.work = base / "vault", base / "vault2", base / "work"
        for v in (self.vault, self.vault2):
            v.mkdir()
            (v / "soul.md").write_text("# Soul\n", encoding="utf-8")
            (v / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        self.work.mkdir()
        self.conf = base / "config.toml"
        self.conf.write_text(f'# keep me\n[tally]\ncore_dir = "{self.vault}"\n', encoding="utf-8")
        cfg = load_config(self.conf)
        cfg.state_dir = base / "state"
        cfg.claude_path = ""
        self.game = Game(cfg)
        self.game.auth.set_password("supersecret")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.game))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, cookie=""):
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}"), ""

    def login(self, role, pw=""):
        return self.call("/api/login", {"role": role, "password": pw})[2].split(";")[0]

    def test_owner_views_and_sets_folders_and_browser(self):
        c = self.login("owner", "supersecret")
        status, s, _ = self.call("/api/settings", cookie=c)
        self.assertEqual(status, 200)
        self.assertTrue(s["core_dir"]["exists"])
        self.assertEqual(s["work_dir"]["path"], "")
        self.assertIn("chrome", s["browsers"])
        status, r, _ = self.call("/api/settings", {"core_dir": str(self.vault2), "work_dir": str(self.work),
                                                   "browser": "chrome"}, cookie=c)
        self.assertEqual(status, 200, r)
        self.assertEqual(sorted(r["changed"]), ["browser", "core_dir", "work_dir"])
        self.assertEqual((self.game.cfg.core_dir, self.game.cfg.work_dir), (self.vault2, self.work))
        self.assertEqual(self.game.ledger.root, self.vault2.resolve())
        again = load_config(self.conf)  # written at once, comments kept
        self.assertEqual((again.core_dir, again.work_dir, again.errand_browser), (self.vault2, self.work, "chrome"))
        self.assertIn("# keep me", self.conf.read_text(encoding="utf-8"))
        # a folder that does not exist is refused, the message does not repeat the path, nothing changes
        missing = str(self.vault.parent / "no-such-vault-xyz")
        status, r, _ = self.call("/api/settings", {"core_dir": missing, "browser": "off"}, cookie=c)
        self.assertEqual(status, 400)
        self.assertNotIn("no-such-vault-xyz", r["error"])
        self.assertEqual(load_config(self.conf).errand_browser, "chrome")
        for body in ({"core_dir": ""}, {"core_dir": "relative/path"}, {"browser": "lynx"}, {"work_dir": missing}):
            self.assertEqual(self.call("/api/settings", body, cookie=c)[0], 400, body)
        status, r, _ = self.call("/api/settings", {"work_dir": ""}, cookie=c)  # the Work vault is optional
        self.assertEqual((status, self.game.cfg.work_dir), (200, None))
        # a folder without the core files is saved, with a warning
        status, r, _ = self.call("/api/settings", {"core_dir": str(self.work)}, cookie=c)
        self.assertEqual(status, 200)
        self.assertTrue(r["notes"])

    def test_guest_sees_no_paths_and_no_owner_settings(self):
        g = self.login("guest")
        self.assertEqual(self.call("/api/settings", cookie=g)[0], 403)
        self.assertEqual(self.call("/api/settings", {"core_dir": str(self.vault2)}, cookie=g)[0], 403)
        self.assertEqual(self.game.cfg.core_dir, self.vault)
        _, world, _ = self.call("/api/world", cookie=g)
        dumped = json.dumps(world)
        self.assertNotIn(str(self.vault), dumped)
        self.assertNotIn(self.vault.name, json.dumps(world["shelves"]))

    def test_character_window_writes_the_local_config(self):
        c = self.login("owner", "supersecret")
        status, _, _ = self.call("/api/character", {"owner": "Robin", "timezone": "Europe/Berlin"}, cookie=c)
        self.assertEqual(status, 200)
        again = load_config(self.conf)
        self.assertEqual((again.owner, again.timezone), ("Robin", "Europe/Berlin"))
        self.assertIn("# keep me", self.conf.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
