"""A tiny local web server: serves the game and streams Tally's replies. Standard library only."""

from __future__ import annotations

import json
import webbrowser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .auth import COOKIE, SESSION_DAYS, Auth, AuthError
from .board import Board, BoardError
from .character import CharacterError, portrait_file
from . import character, ledger as ledger_mod, settings
from .brains import Brain, make_brain, make_guest_brain, write_mcp_config
from .errands import Errands
from .config import REPO_ROOT, Config, load_config, short_path
from .ledger import Ledger, LedgerError
from .moves import MoveLog
from .persona import Persona, guest_shelves, load_guest_persona, load_persona, vault_shelves
from .brass import Brass
from .cat import ACTIONS as CAT_ACTIONS, Cat, CatError
from .tick import Ticker
from .world import World, WorldError

WEB_DIR = REPO_ROOT / "web"


class Game:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.persona: Persona = load_persona(cfg)
        self.brain: Brain = make_brain(cfg, self.persona)
        self.errands = Errands(cfg)
        self.board = Board(cfg)
        self.ledger = Ledger(cfg, on_errand=self.errands.start)
        self.moves = MoveLog(cfg.state_dir, cfg.timezone, cfg.fill)
        self.ticker = Ticker(cfg, self.brain, self.errands)
        self.layout = World(cfg.state_dir)
        self.cat = Cat(cfg.state_dir)
        self.brass = Brass(cfg.state_dir)
        self.auth = Auth(cfg.state_dir)
        self.guest_brain: Brain = make_guest_brain(cfg, load_guest_persona(cfg))

    def world(self, role: str = "owner") -> dict:
        world = self.layout.load()  # the repo's map plus Tally's own changes
        guest = role != "owner"
        world["shelves"] = guest_shelves(self.cfg) if guest else vault_shelves(self.cfg)
        world["role"] = role
        world["names"] = self.names()
        cr = world.get("creatures", {})
        if "player" in cr:
            cr["player"] = {**cr["player"], "name": self.cfg.owner}
            if self.cfg.owner_portrait:
                cr["player"]["portrait"] = {**cr["player"].get("portrait", {}), "image": self.cfg.owner_portrait}
        if "tally" in cr:
            cr["tally"] = {**cr["tally"], "name": self.cfg.companion, "desc": self.cfg.fill(cr["tally"].get("desc", ""))}
        world["cat_saved"] = self.cat.get()  # where the cat was, its mood, and how long since it ate
        world["tally_saved"] = self.layout.tally()  # where Tally was when the game last closed
        world["brass_saved"] = {"present": False} if guest else self.brass_state()  # errands are the owner's
        world["status"] = {
            "brain": self.brain.name,
            "core_loaded": self.persona.loaded,
            "core_missing": self.persona.missing,
            "writes": "off" if guest else self.cfg.writes_mode,
            "tick_minutes": 0 if guest else self.cfg.tick_minutes,
            "errands": not guest and self.cfg.errands and self.cfg.writes_mode != "off",
        }
        if guest:
            world["status"]["brain"] = self.guest_brain.name
            saved = world.get("tally_saved")
            if saved:  # where Tally is, but not why: those reasons can be the owner's business
                saved["doing"] = {**saved.get("doing", {}), "why": ""}
                saved["plan"] = [{**p, "why": ""} for p in saved.get("plan", [])]
        return world


    def apply_character(self) -> None:
        """New names or time zone from the Character window: rebuild what was made with the old ones."""
        self.persona = load_persona(self.cfg)
        self.brain.persona = self.persona
        self.guest_brain.persona = load_guest_persona(self.cfg)
        ledger_mod._TZ = self.cfg.timezone
        self.moves.timezone = self.cfg.timezone

    def apply_settings(self, changed: list[str]) -> None:
        """New vault folders from the Settings window: point everything at them now."""
        if not ({"core_dir", "work_dir"} & set(changed)):
            return
        self.apply_character()  # the system prompt (core files, Work vault line) is rebuilt
        self.ledger.root = self.cfg.core_dir.resolve() if self.cfg.core_dir else None
        if hasattr(self.brain, "mcp_config"):  # the Claude Code brain
            self.brain.mcp_config = write_mcp_config(self.cfg)  # the vault tools get the new folder
        if "core_dir" in changed:
            self.brain.reset()  # a conversation belongs to the folder it ran in; start a fresh one

    def brass_state(self) -> dict:
        """Where Brass should be: the pending ops are read before the records (see Brass.update)."""
        return self.brass.update(self.ledger.pending(), self.errands.all())

    def names(self) -> dict:
        return {"owner": self.cfg.owner, "companion": self.cfg.companion, "owner_portrait": self.cfg.owner_portrait}


def make_handler(game: Game):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB_DIR), **kwargs)

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def end_headers(self):
            # The game's files change when you pull; make the browser check for a newer copy
            # every time instead of mixing cached old scripts with new ones.
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page went away (reload, closed tab) before the answer

        def _bytes(self, body: bytes, ctype: str, sandbox: bool = False):
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                if sandbox:  # a board page runs its own scripts, but never as the game
                    self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-popups")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _token(self) -> str | None:
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except Exception:
                return None
            return c[COOKIE].value if COOKIE in c else None

        def _role(self) -> str | None:
            return game.auth.role(self._token())

        def _login_json(self, data, token: str | None, status=HTTPStatus.OK):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            age = SESSION_DAYS * 86400 if token else 0
            self.send_header("Set-Cookie", f"{COOKIE}={token or ''}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}")
            self.end_headers()
            self.wfile.write(body)

        # Guests may walk around and talk to Tally; the owner's things stay theirs.
        OWNER_ONLY = ("/api/board", "/api/errands", "/api/pending", "/api/tally", "/api/character", "/api/settings",
                      "/api/brass")

        def _gate(self) -> str | None:
            """The role for an /api/ call, or None after answering 401/403 for it."""
            role = self._role()
            if not role:
                self._json({"error": "log in first"}, HTTPStatus.UNAUTHORIZED)
                return None
            if role != "owner" and self.path.startswith(self.OWNER_ONLY):
                self._json({"error": "that is the owner's"}, HTTPStatus.FORBIDDEN)
                return None
            return role

        def do_GET(self):
            if self.path == "/api/login":
                return self._json({"role": self._role(), "has_password": game.auth.has_password(), "names": game.names()})
            if self.path.split("?")[0] == "/" + character.PORTRAIT_URL:
                # the owner's uploaded portrait: on the login card too, so before a login
                p = portrait_file(game.cfg.state_dir)
                if not p:
                    return self._json({"error": "no portrait"}, HTTPStatus.NOT_FOUND)
                return self._bytes(p.read_bytes(), {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[p.suffix[1:]])
            if not self.path.startswith("/api/"):
                return super().do_GET()
            role = self._gate()
            if not role:
                return
            if self.path == "/api/board":
                return self._json({"items": game.board.listing(game.errands.all())})
            if self.path.startswith("/api/board/file/"):
                try:
                    body, ctype = game.board.open_file(self.path.rsplit("/", 1)[1])
                except (BoardError, OSError) as e:
                    return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
                return self._bytes(body, ctype, sandbox=ctype.startswith(("text/html", "image/svg")))
            if self.path.startswith("/api/board/report/"):
                rec = game.errands.get(self.path.rsplit("/", 1)[1])
                if not rec:
                    return self._json({"error": "no such errand"}, HTTPStatus.NOT_FOUND)
                return self._json({k: rec.get(k) for k in ("id", "kind", "title", "brief", "status", "result",
                                                          "branch", "commits", "cost_usd", "approved", "finished")})
            if self.path == "/api/errands":
                return self._json({"errands": [{k: r.get(k) for k in ("id", "kind", "title", "status")}
                                               for r in game.errands.all()[:20]]})
            if self.path == "/api/world":
                return self._json(game.world(role))
            if self.path == "/api/brass":  # owner only (OWNER_ONLY): Brass follows the owner's code errands
                return self._json(game.brass_state())
            if self.path == "/api/character":
                return self._json({**game.names(), "timezone": game.cfg.timezone,
                                   "uploaded": bool(portrait_file(game.cfg.state_dir))})
            if self.path == "/api/settings":  # owner only (OWNER_ONLY): these are the owner's paths
                return self._json(settings.view(game.cfg))
            if self.path == "/api/pending":
                return self._json({"mode": game.cfg.writes_mode, "pending": game.ledger.pending(),
                                   "size": game.ledger.size_report(for_tally=False)})
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 6 * 1024 * 1024:  # a portrait upload is the largest thing the game takes
                return self._json({"error": "too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
            if not isinstance(payload, dict):
                return self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)

            if self.path == "/api/login":
                try:
                    token = game.auth.login(str(payload.get("role", "")), str(payload.get("password", "")),
                                            new_password=bool(payload.get("new_password")))
                except AuthError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.UNAUTHORIZED)
                return self._login_json({"ok": True, "role": game.auth.role(token)}, token)
            if self.path == "/api/logout":
                game.auth.logout(self._token())
                return self._login_json({"ok": True}, None)

            role = self._gate()
            if not role:
                return
            guest = role != "owner"

            if self.path == "/api/character":
                try:
                    if payload.get("new_password"):
                        game.auth.change_password(str(payload.get("old_password", "")), str(payload["new_password"]))
                    character.save(game.cfg, payload)
                except (CharacterError, AuthError) as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                game.apply_character()
                return self._json({"ok": True, **game.names(), "timezone": game.cfg.timezone})

            if self.path == "/api/settings":
                try:
                    result = settings.save(game.cfg, payload)
                except settings.SettingsError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                game.apply_settings(result["changed"])
                return self._json({"ok": True, **result, **settings.view(game.cfg)})

            if self.path == "/api/reset":
                (game.guest_brain if guest else game.brain).reset()
                return self._json({"ok": True})

            if self.path == "/api/tally":
                try:
                    game.layout.save_tally(payload)
                except (WorldError, TypeError, ValueError) as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json({"ok": True})

            if self.path == "/api/cat":  # the browser says where the cat is (OWNER's game only)
                if guest:
                    return self._json({"ok": True, "skipped": "guest"})
                try:
                    return self._json({"ok": True, "cat": game.cat.save(payload)})
                except CatError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)

            if self.path.startswith("/api/cat/") and self.path.split("/")[-1] in CAT_ACTIONS:
                try:
                    res = game.cat.tend("guest" if guest else "owner", self.path.split("/")[-1],
                                        payload.get("at"), payload.get("cat"))
                except CatError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json({**res, "cat": game.cat.get()})

            if self.path == "/api/brass/seen":  # the browser played out Brass's walk to the board or the door
                game.brass.seen_walk(str(payload.get("errand", "")))
                return self._json(game.brass_state())

            if self.path == "/api/tick":
                if game.cfg.tick_minutes <= 0 or guest:
                    return self._json({"go": "stay", "at": None, "say": "", "skipped": "off"})
                result = game.ticker.tick(game.world(), payload)
                if result.get("error"):
                    print(f"  tick failed: {result['error']}")
                return self._json(result)

            if self.path.startswith("/api/pending/"):
                parts = self.path.split("/")  # ["", "api", "pending", id, action]
                if len(parts) != 5 or parts[4] not in ("approve", "reject"):
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                try:
                    if parts[4] == "approve":
                        msg = game.ledger.approve(parts[3])
                    else:
                        game.ledger.reject(parts[3])
                        msg = "Rejected. Nothing was written."
                except LedgerError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.CONFLICT)
                return self._json({"ok": True, "message": msg})

            if self.path != "/api/talk":
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            text = said = str(payload.get("text", "")).strip()
            if not text:
                return self._json({"error": "empty message"}, HTTPStatus.BAD_REQUEST)
            where = str(payload.get("where", "")).strip()[:1000]
            if guest:
                return self._stream(game.guest_brain, f"<game_state>{where}</game_state>\n{text}" if where else text, guest=True)
            game.cat.note_talk(payload.get("cat"), payload.get("tally"))  # what Tally is told, for tend_cat
            finished = game.errands.unreported()
            if finished:
                news = game.cfg.fill("\n\n".join(game.errands.summary(r) for r in finished))
                text = f"<errands_finished>\n{news}\n</errands_finished>\n{text}"
            memory = game.moves.for_prompt(6)
            if memory:
                text = (f"<your_recent_moves>\nYour own recent choices about where to be, written down when you made "
                        f"them (oldest first). The ones made between conversations come from your short check-ins, "
                        f"with a brief sketch of you rather than this whole conversation; they are still your "
                        f"choices and your reasons.\n{memory}\n</your_recent_moves>\n{text}")
            if where:
                text = f"<game_state>{where}</game_state>\n{text}"
            return self._stream(game.brain, text, said=said)

        def _stream(self, brain: Brain, text: str, guest: bool = False, said: str = ""):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            layout_before = game.layout.version()
            if not guest:
                game.layout.take_intent()  # an old intent from a reply that never finished does not count
            reply = []
            try:
                for event in brain.talk(text):
                    if event.get("type") == "delta":
                        reply.append(event.get("text", ""))
                    if event.get("type") == "done" and not guest:
                        game.ticker.last_talk.add(said, "".join(reply))  # the next tick builds on it
                        # Tally's tools said where to be, or changed the room: tell the game before closing.
                        intent = game.layout.take_intent()
                        if intent:
                            game.moves.add("talk", intent.get("kind", ""), intent.get("name", ""),
                                           intent.get("why", ""), room=intent.get("room", ""),
                                           how=intent.get("how", ""), item=intent.get("item", ""))
                            self.wfile.write(f"data: {json.dumps({'type': 'intent', **intent})}\n\n".encode())
                        cat = game.cat.take_pending()  # Tally tended the cat
                        if cat:
                            self.wfile.write(f"data: {json.dumps({'type': 'cat', 'events': cat})}\n\n".encode())
                        if game.layout.version() != layout_before:
                            self.wfile.write(f"data: {json.dumps({'type': 'world'})}\n\n".encode())
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def main() -> None:
    cfg = load_config()
    game = Game(cfg)
    p = game.persona
    print("The Obsidian Vault")
    print(f"  brain: {game.brain.name}")
    print(f"  settings: {short_path(cfg.config_path)} (outside the game folder; Esc → Settings in the game)")
    print(f"  names: {cfg.owner} (owner), {cfg.companion} (companion)")
    if cfg.owner == "Owner":
        print("  note: set your name in the game (Esc → Character)")
    if game.brain.name == "claude-code":
        if cfg.claude_path:
            print(f"  claude: {cfg.claude_path}")
        else:
            from .brains import CLAUDE_MISSING
            print(f"  WARNING: {CLAUDE_MISSING}")
    if p.ok:
        print(f"  {cfg.companion}'s core: {', '.join(p.loaded)} from {short_path(cfg.core_dir)}")
    else:
        print(f"  WARNING: Tally's core is incomplete. Missing: {', '.join(p.missing)}")
        print("  Set the Private vault folder in the game (Esc → Settings), or tally.core_dir in your settings file.")
    modes = {"off": "off (read-only)", "review": ("review (note changes wait for you in the game; soul.md is Tally's own)" if cfg.soul_direct
                       else "review (every change waits for you in the game)"),
             "direct": "direct (soul.md at once; note changes wait for you)"}
    print(f"  writing: {modes.get(cfg.writes_mode, cfg.writes_mode)}")
    if cfg.tick_minutes > 0:
        print(f"  tick: Tally decides where to go every {cfg.tick_minutes:g} min ({cfg.tick_model or 'default model'})")
    else:
        print("  tick: off")
    url = f"http://{cfg.host}:{cfg.port}/"
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(game))
    print(f"  open {url}  (Ctrl+C to leave)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nYou leave the vault. Tally keeps the ledger.")


if __name__ == "__main__":
    main()
