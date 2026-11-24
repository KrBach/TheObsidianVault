"""A tiny local web server: serves the game and streams Tally's replies. Standard library only."""

from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .brains import Brain, make_brain
from .config import REPO_ROOT, Config, load_config
from .persona import Persona, load_persona, vault_shelves

WEB_DIR = REPO_ROOT / "web"
WORLD_FILE = REPO_ROOT / "world" / "vault.json"
TILES_FILE = REPO_ROOT / "world" / "tiles.json"


class Game:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.persona: Persona = load_persona(cfg)
        self.brain: Brain = make_brain(cfg, self.persona)

    def world(self) -> dict:
        world = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
        world.update(json.loads(TILES_FILE.read_text(encoding="utf-8")))
        world["shelves"] = vault_shelves(self.cfg)
        world["status"] = {
            "brain": self.brain.name,
            "core_loaded": self.persona.loaded,
            "core_missing": self.persona.missing,
        }
        return world


def make_handler(game: Game):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB_DIR), **kwargs)

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/world":
                return self._json(game.world())
            return super().do_GET()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")

            if self.path == "/api/reset":
                game.brain.reset()
                return self._json({"ok": True})

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
            try:
                for event in game.brain.talk(text):
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
