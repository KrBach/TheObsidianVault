"""The PII guard: no tracked file may hold a home-folder path, a real email address or a phone number.

The repo is public. Personal paths and data belong in the local config (see obsidian_vault/config.py),
never in the code, the docs or the tests. Placeholders are fine: /Users/<name>, ~/path/to/...,
anything @example.com, and the bot addresses git and GitHub use.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Built from pieces so this file does not trip its own checks.
_U, _H = "Users", "home"
HOME_PATH = re.compile(r"(?:/" + _U + r"/|/" + _H + r"/|[A-Za-z]:\\" + _U + r"\\)(?!<|\$|%|\{|USER|NAME|you\b|me\b|name\b|runner\b|Shared\b)[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
ALLOWED_EMAIL = re.compile(r"@(?:(?:[A-Za-z0-9-]+\.)*example\.(?:com|org|net)|example|anthropic\.com|"
                           r"users\.noreply\.github\.com|noreply\.github\.com)$", re.I)
PHONE = [
    re.compile(r"(?<![\w.])\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}(?![\w.])"),  # international, with +
    re.compile(r"(?<![\w.:-])\d{2} \d{2} \d{2} \d{2}(?![\w.:-])"),                       # four pairs of digits
    re.compile(r"(?<![\w.-])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.-])"),                  # 3-3-4 digits
]
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".ico", ".woff", ".woff2"}
NEVER_TRACKED = {"config.toml", "settings.json", "login.json", "sessions.json"}


def tracked_files() -> list[Path]:
    """Files in the repo, and new ones not yet added that git would not ignore."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, check=True).stdout
        return [ROOT / p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):  # not a git checkout (a release download): walk it
        skip = {".git", ".vault-state", "__pycache__", ".venv"}
        found = []
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in skip]
            found += [Path(d) / f for f in files if f not in ("config.toml",) and not f.startswith("config.toml.")]
        return found


def findings(text: str) -> list[str]:
    found = [m.group(0) for m in HOME_PATH.finditer(text)]
    found += [m.group(0) for m in EMAIL.finditer(text) if not ALLOWED_EMAIL.search(m.group(0))]
    for rx in PHONE:
        found += [m.group(0) for m in rx.finditer(text)]
    return found


class PIIGuardTest(unittest.TestCase):
    def test_the_patterns(self):
        u = "/" + _U + "/"
        self.assertTrue(findings(f"core_dir = \"{u}robin/Vault\""))
        self.assertTrue(findings("C:\\" + _U + "\\robin\\vault"))
        self.assertTrue(findings("mail robin" + "@" + "proton.me"))
        pairs = " ".join(["12", "34", "56", "78"])  # made-up numbers, in pieces so this file passes its own check
        self.assertTrue(findings("call +45 " + pairs))
        self.assertTrue(findings(f"call {pairs} now"))
        self.assertTrue(findings("call (555) " + "123" + "-" + "4567"))
        for fine in (f"{u}<name>/Vault", "~/path/to/obsidian/Private", "t" + "@" + "example.com",
                     "noreply" + "@" + "anthropic.com", "2026-11-25 10:00", "betas=2026-07-01", "[18, 0, 17, 9]",
                     "version 1.2.3", "port 8765", "3 * 1024 * 1024"):
            self.assertEqual(findings(fine), [], fine)

    def test_no_personal_data_in_tracked_files(self):
        problems = []
        for path in tracked_files():
            rel = path.relative_to(ROOT).as_posix()
            if path.name in NEVER_TRACKED or rel.startswith(".vault-state/"):
                problems.append(f"{rel}: this file is local and must not be in the repo")
                continue
            if path.suffix.lower() in BINARY or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for hit in findings(line):
                    # the hit itself is not printed in full: a failing test log must not spread it further
                    problems.append(f"{rel}:{n}: looks like personal data ({hit[:6]}…)")
        self.assertEqual(problems, [], "Move these to the local config (see config.py) or use a placeholder.")


if __name__ == "__main__":
    unittest.main()
