"""Tests for the login: the owner's password, guests, and what a guest may not reach.

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

from obsidian_vault.auth import Auth, AuthError, hash_password, verify_password  # noqa: E402
from obsidian_vault.config import Config  # noqa: E402
from obsidian_vault.server import Game, make_handler  # noqa: E402


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Auth(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_never_holds_the_password(self):
        h = hash_password("supersecret")
        self.assertNotIn("supersecret", h)
        self.assertTrue(verify_password("supersecret", h))
        self.assertFalse(verify_password("supersecreT", h))
        self.assertFalse(verify_password("x", "garbage"))

    def test_first_password_then_login(self):
        self.assertFalse(self.auth.has_password())
        with self.assertRaises(AuthError):
            self.auth.login("owner", "supersecret")  # nothing set yet
        with self.assertRaises(AuthError):
            self.auth.login("owner", "short", new_password=True)
        tok = self.auth.login("owner", "supersecret", new_password=True)
        self.assertEqual(self.auth.role(tok), "owner")
        self.assertNotIn("supersecret", self.auth.login_file.read_text())
        with self.assertRaises(AuthError):  # the first-time path cannot replace a set password
            self.auth.login("owner", "another one", new_password=True)
        with self.assertRaises(AuthError):
            self.auth.login("owner", "wrong")
        self.assertEqual(self.auth.role(self.auth.login("owner", "supersecret")), "owner")
        self.assertNotIn(tok, self.auth.sessions_file.read_text())  # only hashes of tokens are kept
        self.assertEqual(Auth(Path(self.tmp.name)).role(tok), "owner")  # survives a restart
        self.auth.logout(tok)
        self.assertIsNone(self.auth.role(tok))

    def test_guest_needs_no_password(self):
        self.assertEqual(self.auth.role(self.auth.login("guest")), "guest")
        with self.assertRaises(AuthError):
            self.auth.login("admin")
        self.assertIsNone(self.auth.role("made-up"))


class ServerGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        vault = base / "vault"
        (vault / "health").mkdir(parents=True)
        (vault / "soul.md").write_text("# Soul\nSECRET-SOUL\n", encoding="utf-8")
        (vault / "bootstrap.md").write_text("# Bootstrap\n", encoding="utf-8")
        (vault / "health" / "n.md").write_text("x", encoding="utf-8")
        cfg = Config(core_dir=vault, state_dir=base / "state", claude_path="", writes_folders=["health"])
        self.game = Game(cfg)
        self.game.auth.set_password("supersecret")
        self.game.layout.save_tally({"x": 20, "y": 4, "doing": {"kind": "stay", "why": "PRIVATE-REASON"}})
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
            return e.code, json.loads(e.read() or b"{}"), ""

    def login(self, role, pw=""):
        status, data, set_cookie = self.call("/api/login", {"role": role, "password": pw})
        self.assertEqual(status, 200, data)
        self.assertIn("HttpOnly", set_cookie)
        return set_cookie.split(";")[0]

    def test_nothing_without_a_login(self):
        self.assertEqual(self.call("/api/world")[0], 401)
        self.assertEqual(self.call("/api/talk", {"text": "hi"})[0], 401)
        self.assertEqual(self.call("/api/login", {"role": "owner", "password": "nope"})[0], 401)
        status, data, _ = self.call("/api/login")
        self.assertEqual((status, data["role"], data["has_password"]), (200, None, True))

    def test_owner_gets_everything(self):
        c = self.login("owner", "supersecret")
        status, world, _ = self.call("/api/world", cookie=c)
        self.assertEqual((status, world["role"]), (200, "owner"))
        self.assertIn("health", [s["name"] for s in world["shelves"]])
        self.assertEqual(world["tally_saved"]["doing"]["why"], "PRIVATE-REASON")
        self.assertEqual(self.call("/api/pending", cookie=c)[0], 200)
        self.assertEqual(self.call("/api/board", cookie=c)[0], 200)

    def test_guest_is_kept_out_of_the_owners_things(self):
        c = self.login("guest")
        status, world, _ = self.call("/api/world", cookie=c)
        self.assertEqual((status, world["role"]), (200, "guest"))
        self.assertNotIn("health", json.dumps(world["shelves"]))
        self.assertEqual(world["tally_saved"]["doing"]["why"], "")
        self.assertEqual(world["status"]["writes"], "off")
        self.assertEqual(world["status"]["tick_minutes"], 0)
        for path in ("/api/pending", "/api/board", "/api/errands"):
            self.assertEqual(self.call(path, cookie=c)[0], 403, path)
        self.assertEqual(self.call("/api/pending/abc/approve", {}, cookie=c)[0], 403)
        self.assertEqual(self.call("/api/tally", {"x": 1, "y": 1}, cookie=c)[0], 403)
        self.assertEqual(self.call("/api/tick", {}, cookie=c)[1].get("skipped"), "off")
        # the guest's Tally has none of the private core
        self.assertNotIn("SECRET-SOUL", self.game.guest_brain.persona.system_prompt)
        self.assertIn("SECRET-SOUL", self.game.brain.persona.system_prompt)
        cmd = self.game.guest_brain._command()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--mcp-config", cmd)
        self.assertNotEqual(Path(self.game.guest_brain._cwd()).resolve(), self.game.cfg.core_dir.resolve())

    def test_character_settings(self):
        import base64
        c = self.login("owner", "supersecret")
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nrest").decode()
        status, data, _ = self.call("/api/character", {"owner": "Robin", "companion": "Quill",
                                                       "timezone": "Europe/Berlin", "portrait": png}, cookie=c)
        self.assertEqual((status, data["owner"], data["companion"], data["owner_portrait"]), (200, "Robin", "Quill", "portrait/owner"))
        self.assertIn("Quill", self.game.guest_brain.persona.system_prompt)
        with urllib.request.urlopen(self.url + "/portrait/owner") as r:  # shown on the login card, so no login
            self.assertTrue(r.read().startswith(b"\x89PNG"))
        from obsidian_vault.config import load_config
        import os
        cfg_path = Path(self.tmp.name) / "c.toml"
        cfg_path.write_text(f'[writes]\nstate_dir = "{self.game.cfg.state_dir}"\n', encoding="utf-8")
        again = load_config(cfg_path)  # a new process (the vault tools) sees the same names
        self.assertEqual((again.owner, again.companion, again.timezone), ("Robin", "Quill", "Europe/Berlin"))
        bad = [{"owner": ""}, {"timezone": "Mars/Olympus"}, {"portrait": "data:image/png;base64," + base64.b64encode(b"GIF89a").decode()},
               {"portrait": "data:text/html;base64,PGI+"}, {"old_password": "nope", "new_password": "another1"}]
        for body in bad:
            self.assertEqual(self.call("/api/character", body, cookie=c)[0], 400, body)
        self.assertEqual(self.call("/api/character", {"old_password": "supersecret", "new_password": "newsecret"}, cookie=c)[0], 200)
        self.assertEqual(self.call("/api/login", {"role": "owner", "password": "supersecret"})[0], 401)
        self.assertEqual(self.call("/api/login", {"role": "owner", "password": "newsecret"})[0], 200)
        g = self.login("guest")
        self.assertEqual(self.call("/api/character", {"owner": "Mallory"}, cookie=g)[0], 403)
        self.assertEqual(self.call("/api/character", cookie=g)[0], 403)

    def test_logout(self):
        c = self.login("guest")
        self.call("/api/logout", {}, cookie=c)
        self.assertEqual(self.call("/api/world", cookie=c)[0], 401)


if __name__ == "__main__":
    unittest.main()
