"""A tiny local web server: serves the game and streams Tally's replies. Standard library only."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .auth import COOKIE, SESSION_DAYS, Auth, AuthError
from .board import Board, BoardError
from .character import CharacterError, portrait_file
from . import character, ledger as ledger_mod, settings
from .brains import Brain, make_brain, make_guest_brain, write_mcp_config
from .errands import ErrandError, Errands
from .config import REPO_ROOT, Config, load_config, short_path
from .ledger import Ledger, LedgerError
from .moves import MoveLog
from .persona import Persona, guest_shelves, load_guest_persona, load_persona, vault_shelves
from .rotation import Rotation
from .brass import Brass, BrassTalk
from .cat import ACTIONS as CAT_ACTIONS, Cat, CatError
from .scheduler import Scheduler, queue_status, queue_view
from .tick import Ticker
from .usage import Meter
from .vellum import VellumParser, tile_at
from .world import World, WorldError

WEB_DIR = REPO_ROOT / "web"


class Game:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.persona: Persona = load_persona(cfg)
        self.brain: Brain = make_brain(cfg, self.persona)
        self.meter = Meter(cfg)
        self.errands = Errands(cfg, on_finish=lambda: self.scheduler.poke())
        self.scheduler = Scheduler(cfg, self.errands, self.meter)  # its timer starts in main()
        for title in self.errands.close_interrupted():  # cut off by the last restart
            print(f"  errand interrupted by the restart: {title}")
        self.board = Board(cfg)
        self.ledger = Ledger(cfg, on_errand=self.errands.start, on_schedule=self.schedule_errand)
        self.moves = MoveLog(cfg.state_dir, cfg.timezone, cfg.fill)
        self.ticker = Ticker(cfg, self.brain, self.errands, queue_note=self.tick_queue_note)
        self.layout = World(cfg.state_dir)
        self.cat = Cat(cfg.state_dir)
        self.vellum = VellumParser(self.cat, companion=cfg.companion)
        self.brass = Brass(cfg.state_dir)
        self.brass_talk = BrassTalk(cfg, self.brain)
        self.auth = Auth(cfg.state_dir)
        self.guest_brain: Brain = make_guest_brain(cfg, load_guest_persona(cfg))
        self.rotation = Rotation(cfg, self.brain)  # when OWNER's conversation closes, and the handoff
        self.server = None  # the HTTP server, so Quit can stop it (set in main)

    def shutdown(self) -> None:
        """Stop serving, from a thread of its own: shutdown() waits for serve_forever to return."""
        self.rotation.stop()
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, name="quit", daemon=True).start()

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

    def schedule_errand(self, op: dict) -> str:
        """Approved "when there's room": into the queue, and a check soon (the scheduler starts it if it fits)."""
        msg = self.errands.schedule(op)
        self.scheduler.poke()
        return msg

    def queue_text(self) -> str:
        return self.cfg.fill(queue_status(self.cfg, self.errands, self.meter))

    def tick_queue_note(self) -> str:
        """For a tick: the meter, and the queue when anything is in it. Read-only, like queue_status."""
        if self.errands.running() or self.errands.scheduled():
            return queue_status(self.cfg, self.errands, self.meter)
        return self.meter.line()

    def pending_with_estimates(self) -> list[dict]:
        """The ledger's pending changes, with the projected cost on each errand."""
        ops = self.ledger.pending()
        for op in ops:
            if op.get("kind") == "errand":
                est, runs = self.errands.projection(op.get("errand_kind", ""))
                op["estimate"], op["estimate_runs"] = round(est, 2), runs
        return ops

    def brass_state(self) -> dict:
        """Where Brass should be, from the errand records (see Brass.update)."""
        return self.brass.update(self.errands.all())

    def names(self) -> dict:
        return {"owner": self.cfg.owner, "companion": self.cfg.companion, "owner_portrait": self.cfg.owner_portrait}

    def cat_parse(self, payload: dict, role: str) -> dict:
        """A '>' line for Vellum: the scripted parser (vellum.py), never a model. The gift's archive
        titles are only reachable for the owner; a guest's parser has no archive at all."""
        guest = role != "owner"
        self.vellum.companion = self.cfg.companion
        self.vellum.archive_dir = None if guest or not self.cfg.core_dir else self.cfg.core_dir / "archive"
        cat = payload.get("cat") if isinstance(payload.get("cat"), dict) else None
        where = cat or self.cat.get()
        res = self.vellum.respond(str(payload.get("text", "")), "guest" if guest else "owner", payload.get("at"), cat,
                                  waiting=0 if guest else len(self.ledger.pending()),
                                  tile=tile_at(self.layout.load(), where.get("x"), where.get("y")))
        return {**res, "cat": self.cat.get()}


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
                      "/api/brass", "/api/queue", "/api/quit")

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
                items = game.board.listing(game.errands.all())
                if game.cfg.errands:  # the queue's own paper: what runs, what waits, and the meter
                    v = queue_view(game.cfg, game.errands, game.meter)
                    items.insert(0, {"id": "queue", "title": "Errand queue", "kind": "queue",
                                     "where": f"{len(v['running'])} running · {len(v['scheduled'])} scheduled",
                                     "updated": 0})
                return self._json({"items": items})
            if self.path == "/api/queue":  # owner only (OWNER_ONLY)
                return self._json({**queue_view(game.cfg, game.errands, game.meter), "text": game.queue_text()})
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
                return self._json({**{k: rec.get(k) for k in ("id", "kind", "title", "brief", "status", "result",
                                                            "branch", "commits", "cost_usd", "est_usd", "tokens",
                                                            "approved", "finished")},
                                   "cost_line": game.errands.cost_line(rec)})
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
                return self._json({"mode": game.cfg.writes_mode, "pending": game.pending_with_estimates(),
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
                if guest:
                    game.guest_brain.reset()
                    return self._json({"ok": True})
                game.rotation.touch()
                res = game.rotation.close("new")  # Tally's last turn: log line and handoff, then a fresh one
                return self._json({"ok": True, **res})

            if self.path == "/api/activity":  # OWNER did something in the game: the idle timer starts again
                if not guest:
                    game.rotation.touch()
                return self._json({"ok": True})

            if self.path == "/api/quit":  # owner only (OWNER_ONLY): the menu's Quit, or the front door
                cue = str(payload.get("cue", "quit"))
                cue = cue if cue in ("quit", "door") else "quit"
                res = game.rotation.close(cue)
                self._json({"ok": True, **res})
                game.shutdown()
                return

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

            if self.path == "/api/cat/parse":  # a '>' line for Vellum: scripted, never a model
                return self._json(game.cat_parse(payload, role))

            # A '>' line is always for the parser, whoever the talk box was open to: it never reaches a model.
            if self.path in ("/api/talk", "/api/brass/talk") and str(payload.get("text", "")).lstrip().startswith(">"):
                res = game.cat_parse(payload, role)
                return self._json({**res, "reply": res["text"], "parser": True})

            if self.path.startswith("/api/cat/") and self.path.split("/")[-1] in CAT_ACTIONS:
                try:
                    res = game.cat.tend("guest" if guest else "owner", self.path.split("/")[-1],
                                        payload.get("at"), payload.get("cat"))
                except CatError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                return self._json({**res, "cat": game.cat.get()})

            if self.path == "/api/brass/talk":  # owner only (OWNER_ONLY): a line to Brass, answered by a cheap model
                try:
                    reply = game.brass_talk.reply(str(payload.get("text", "")), game.brass_state(),
                                                  game.errands.all(), game.errands.summary)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
                except Exception as e:  # noqa: BLE001 - the model call failed: say so in the game
                    return self._json({"ok": False, "error": f"Brass did not answer ({type(e).__name__}: {str(e)[:200]})"},
                                      HTTPStatus.BAD_GATEWAY)
                return self._json({"ok": True, "reply": reply})

            if self.path == "/api/brass/seen":  # the browser played out Brass's walk to the notice board
                game.brass.seen_walk(str(payload.get("errand", "")))
                return self._json(game.brass_state())

            if self.path == "/api/brass/fetched":  # the imp reached Brass: he walks to the Outbox table
                game.brass.fetched(str(payload.get("errand", "")))
                return self._json(game.brass_state())

            if self.path == "/api/tick":
                if game.cfg.tick_minutes <= 0 or guest:
                    return self._json({"go": "stay", "at": None, "say": "", "skipped": "off"})
                result = game.ticker.tick(game.world(), payload)
                if result.get("error"):
                    print(f"  tick failed: {result['error']}")
                return self._json(result)

            if self.path.startswith("/api/queue/"):  # OWNER's priority: one place up or down
                parts = self.path.split("/")  # ["", "api", "queue", id, "up" | "down"]
                if len(parts) != 5 or parts[4] not in ("up", "down"):
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                try:
                    game.errands.move(parts[3], parts[4])
                except ErrandError as e:
                    return self._json({"ok": False, "error": str(e)}, HTTPStatus.CONFLICT)
                return self._json({"ok": True, **queue_view(game.cfg, game.errands, game.meter)})

            if self.path.startswith("/api/pending/"):
                parts = self.path.split("/")  # ["", "api", "pending", id, action]
                if len(parts) != 5 or parts[4] not in ("approve", "reject"):
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                try:
                    if parts[4] == "approve":
                        msg = game.ledger.approve(parts[3], "room" if payload.get("when") == "room" else "now")
                    else:
                        game.ledger.reject(parts[3])
                        msg = "Rejected. Nothing was written."
                except LedgerError as e:
                    print(f"  ledger: could not {parts[4]} {parts[3]}: {e}")
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
            return self._talk_owner(payload, text, said, where)

        def _talk_owner(self, payload: dict, text: str, said: str, where: str):
            """OWNER's message: close the conversation first on a day or size cue, then the turn itself,
            with the handoff in front of the first message of a fresh conversation."""
            rot = game.rotation
            rot.touch()
            self._start_stream()
            with rot.lock:  # waits for an idle close in progress
                cue = rot.due_before_message()
                if cue:
                    self._event({"type": "action", "text": game.cfg.fill(
                        "Tally closes the ledger on this conversation and opens a fresh page "
                        f"({'a new day' if cue == 'day' else 'the conversation has grown long'})")})
                    res = rot.close(cue)
                    if res.get("error"):
                        print(f"  closing turn ({cue}) did not finish: {res['error'][:200]}")
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
            if game.cfg.errands:  # the usage meter, one line, after the cap on what the browser sends
                try:
                    where = f"{where} {game.meter.line()}".strip()
                except Exception:  # noqa: BLE001 - a meter that cannot read never stops the talk
                    pass
            where = f"{where} {rot.size_line()}".strip()  # the conversation's approximate size, for Tally
            text = f"<game_state>{where}</game_state>\n{text}"
            text, injected = rot.first_turn_text(text)  # Tally's own handoff, once, in a fresh conversation
            rot.begin_turn()
            try:
                ok = self._stream(game.brain, text, said=said, started=True)
            finally:
                rot.end_turn()
            rot.turn_done(ok, injected)

        def _start_stream(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def _event(self, event: dict):
            try:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _stream(self, brain: Brain, text: str, guest: bool = False, said: str = "", started: bool = False) -> bool:
            """Stream one turn to the browser. Returns True if it finished without an error."""
            if not started:
                self._start_stream()
            layout_before = game.layout.version()
            if not guest:
                game.layout.take_intent()  # an old intent from a reply that never finished does not count
            reply = []
            ok, done = True, False
            try:
                for event in brain.talk(text):
                    if event.get("type") == "error":
                        ok = False
                    if event.get("type") == "done":
                        done = True
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
                pass  # the page went away; the turn itself may still have finished
            return ok and done

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
    if cfg.errands:
        game.scheduler.start()  # plain code on a timer: starts scheduled errands when there is room
        print(f"  errand queue: checks every {game.scheduler.interval / 60:g} min; block budget "
              f"${cfg.usage_block_budget:g}, daily cap ${cfg.usage_daily_cap:g} (estimates at API prices)")
    idle = f"after {cfg.idle_minutes:g} min without input" if cfg.idle_minutes > 0 else "never when idle"
    limit = f", past {cfg.context_limit_tokens // 1000}K tokens" if cfg.context_limit_tokens else ""
    print(f"  conversation: closes on Quit, at the front door, at 04:00, {idle}{limit} (with a handoff)")
    url = f"http://{cfg.host}:{cfg.port}/"
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(game))
    game.server = server
    game.rotation.watch()
    print(f"  open {url}  (Quit in the menu, or Ctrl+C, to leave)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
        print("You leave the vault by the front door. Tally keeps the ledger.")
    except KeyboardInterrupt:
        print("\nYou leave the vault. Tally keeps the ledger.")
    finally:
        game.rotation.stop()
        server.server_close()


if __name__ == "__main__":
    main()
