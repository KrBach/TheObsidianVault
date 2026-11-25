"""Who is in the vault: Kristian (with a password) or a guest.

The password is never stored, only a salted PBKDF2 hash in <state_dir>/login.json (git-ignored).
The first time the game opens, the login screen asks Kristian to choose one. To change it later:

    python3 -m obsidian_vault.auth set-password

A login hands the browser a random session token in an HttpOnly cookie. Sessions are kept in
<state_dir>/sessions.json so a restart of the game does not log you out; they last 30 days.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

ROLES = ("kristian", "guest")
ITERATIONS = 310_000
SESSION_DAYS = 30
MIN_PASSWORD = 6
COOKIE = "vault_session"


class AuthError(Exception):
    pass


def hash_password(pw: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        mine = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Auth:
    def __init__(self, state_dir: Path):
        self.login_file = Path(state_dir) / "login.json"
        self.sessions_file = Path(state_dir) / "sessions.json"
        self.lock = threading.Lock()
        self.failures = 0

    # ---------- the password ----------

    def _stored(self) -> str:
        try:
            return json.loads(self.login_file.read_text(encoding="utf-8")).get("kristian", "")
        except (OSError, ValueError):
            return ""

    def has_password(self) -> bool:
        return bool(self._stored())

    def set_password(self, pw: str, first_time: bool = False) -> None:
        if len(pw or "") < MIN_PASSWORD:
            raise AuthError(f"the password needs at least {MIN_PASSWORD} characters")
        with self.lock:
            if first_time and self.has_password():
                raise AuthError("a password is already set")
            _write(self.login_file, {"kristian": hash_password(pw)})

    # ---------- sessions ----------

    def _sessions(self) -> dict:
        try:
            data = json.loads(self.sessions_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = time.time()
        return {k: v for k, v in data.items() if v.get("until", 0) > now and v.get("role") in ROLES}

    def _new_session(self, role: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            sessions = self._sessions()
            # store only a hash of the token, so the file alone cannot be used to log in
            sessions[hashlib.sha256(token.encode()).hexdigest()] = {"role": role, "until": time.time() + SESSION_DAYS * 86400}
            _write(self.sessions_file, sessions)
        return token

    def login(self, role: str, password: str = "", new_password: bool = False) -> str:
        """Check who is coming in and return a session token."""
        if role == "guest":
            return self._new_session("guest")
        if role != "kristian":
            raise AuthError("no such character")
        if new_password:
            self.set_password(password, first_time=True)
            return self._new_session("kristian")
        if not self.has_password():
            raise AuthError("no password is set yet; choose one first")
        if self.failures >= 3:
            time.sleep(min(2 * (self.failures - 2), 10))  # slow down guessing
        if not verify_password(password or "", self._stored()):
            self.failures += 1
            raise AuthError("that is not the password")
        self.failures = 0
        return self._new_session("kristian")

    def role(self, token: str | None) -> str | None:
        if not token:
            return None
        rec = self._sessions().get(hashlib.sha256(token.encode()).hexdigest())
        return rec["role"] if rec else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.lock:
            sessions = self._sessions()
            sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            _write(self.sessions_file, sessions)

    def logout_all(self) -> None:
        with self.lock:
            _write(self.sessions_file, {})


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["set-password"]:
        print("usage: python3 -m obsidian_vault.auth set-password")
        raise SystemExit(2)
    from .config import load_config

    auth = Auth(load_config().state_dir)
    pw = getpass.getpass("New password for Kristian: ")
    if pw != getpass.getpass("Again: "):
        raise SystemExit("The two did not match. Nothing changed.")
    try:
        auth.set_password(pw)
    except AuthError as e:
        raise SystemExit(str(e))
    auth.logout_all()
    print("Password changed. Everyone is logged out of the game.")


if __name__ == "__main__":
    main()
