"""A tiny local web server: serves the game and streams Tally's replies. Standard library only."""

from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .brains import Brain, make_brain
from .config import REPO_ROOT, Config, load_config
from .ledger import Ledger, LedgerError
from .persona import Persona, load_persona, vault_shelves
from .tick import Ticker
from .world import World

WEB_DIR = REPO_ROOT / "web"


class Game:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.persona: Persona = load_persona(cfg)
        self.brain: Brain = make_brain(cfg, self.persona)
        self.ledger = Ledger(cfg)
        self.ticker = Ticker(cfg, self.brain)
        self.layout = World(cfg.state_dir)

    def world(self) -> dict:
        world = self.layout.load()  # the repo's map plus Tally's own changes
        world["shelves"] = vault_shelves(self.cfg)
        world["status"] = {
            "brain": self.brain.name,
            "core_loaded": self.persona.loaded,
            "core_missing": self.persona.missing,
            "writes": self.cfg.writes_mode,
            "tick_minutes": self.cfg.tick_minutes,
        }
        return world


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

        def do_GET(self):
            if self.path == "/api/world":
                return self._json(game.world())
            if self.path == "/api/pending":
                return self._json({"mode": game.cfg.writes_mode, "pending": game.ledger.pending(),
                                   "size": game.ledger.size_report(for_tally=False)})
            return super().do_GET()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")

            if self.path == "/api/reset":
                game.brain.reset()
                return self._json({"ok": True})

            if self.path == "/api/tick":
                if game.cfg.tick_minutes <= 0:
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

            text = str(payload.get("text", "")).strip()
            if not text:
                return self._json({"error": "empty message"}, HTTPStatus.BAD_REQUEST)
            where = str(payload.get("where", "")).strip()
            if where:
                text = f"<game_state>{where}</game_state>\n{text}"

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            layout_before = game.layout.version()
            game.layout.take_intent()  # an old intent from a reply that never finished does not count
            try:
                for event in game.brain.talk(text):
                    if event.get("type") == "done":
                        # Tally's tools said where to be, or changed the room: tell the game before closing.
                        intent = game.layout.take_intent()
                        if intent:
                            self.wfile.write(f"data: {json.dumps({'type': 'intent', **intent})}\n\n".encode())
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
    if game.brain.name == "claude-code":
        if cfg.claude_path:
            print(f"  claude: {cfg.claude_path}")
        else:
            from .brains import CLAUDE_MISSING
            print(f"  WARNING: {CLAUDE_MISSING}")
    if p.ok:
        print(f"  Tally's core: {', '.join(p.loaded)} from {cfg.core_dir}")
    else:
        print(f"  WARNING: Tally's core is incomplete. Missing: {', '.join(p.missing)}")
        print("  Set tally.core_dir in config.toml (copy config.example.toml).")
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
