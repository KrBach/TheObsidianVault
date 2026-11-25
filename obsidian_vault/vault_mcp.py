"""A tiny MCP server (stdio, no packages) that gives Tally the game's tools.

Claude Code starts it with `python3 -m obsidian_vault.vault_mcp`. Two kinds of tools:
- the game tools (always): where Tally goes or stays, and moving furniture in the vault
  (world.py, which runs every change through the map check);
- the vault-writing tools (when writes.mode is not "off"), all through the Ledger (ledger.py).
"""

from __future__ import annotations

import json
import sys

from .config import load_config
from .board import Board, BoardError
from .errands import Errands
from .ledger import Ledger, LedgerError
from .world import World, WorldError

PROTOCOL = "2025-06-18"


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}
N = {"type": "number"}

TOOLS = [
    {
        "name": "append_interaction_log",
        "description": (
            "Append one line to the Interaction log in soul.md, following bootstrap.md: how the interaction "
            "went (decisions, corrections, rules, Kristian's reactions), not the work itself. The timestamp and "
            "'Project: The Obsidian Vault · Theme: ...' prefix are added for you. At most one entry per session "
            "and theme unless something changes how you should behave or he signals he is stopping."
        ),
        "inputSchema": _schema({
            "theme": {**S, "description": "The theme, e.g. 'Obsidian' or 'Games'."},
            "entry": {**S, "description": "One short paragraph, plain text, no line breaks."},
            "theme_link": {**S, "description": "Optional vault path of the theme's note, e.g. 'games/the-obsidian-vault.md'."},
        }, ["theme", "entry"]),
    },
    {
        "name": "add_coaching_item",
        "description": "Add one line under 'For the coaching thread' in soul.md, for something worth discussing in the coaching thread. Mention it to Kristian at a natural pause.",
        "inputSchema": _schema({"item": {**S, "description": "One line, plain text."}}, ["item"]),
    },
    {
        "name": "edit_soul",
        "description": (
            "Replace one exact passage in soul.md. soul.md is yours: the change is written at once (with a backup), "
            "no approval needed. `old` must occur exactly once. Keep to what the core says belongs in soul.md."
        ),
        "inputSchema": _schema({
            "old": {**S, "description": "The exact text to replace."},
            "new": {**S, "description": "The replacement text."},
            "reason": {**S, "description": "One line on why, kept in the journal."},
        }, ["old", "new", "reason"]),
    },
    {
        "name": "log_failure",
        "description": (
            "Add a numbered entry to agent-failure-log.md, as the Failure log loop rule in soul.md asks: in the same "
            "session as a correction from Kristian or a mistake you catch yourself. The file is yours, so it is "
            "written at once, with a backup. Write 'Repeat of N' in `what` when it repeats an earlier entry."
        ),
        "inputSchema": _schema({
            "date": {**S, "description": "When it happened, e.g. '2026-11-25'. Empty for today."},
            "what": {**S, "description": "What went wrong, one or two sentences."},
            "caught": {**S, "description": "How it was caught, e.g. 'Caught by Kristian.'"},
            "fix": {**S, "description": "The fix or rule."},
            "category": {**S, "description": "One of: source, ui-read, instruction, process, criterion, self-record, unchecked-claim."},
            "operative": {**S, "description": "Where the fix now lives and changes what you do, or 'no (log only)'."},
        }, ["what", "caught", "fix", "category", "operative"]),
    },
    {
        "name": "propose_edit",
        "description": (
            "Propose replacing one exact passage in an existing note in a theme folder. `old` must occur exactly "
            "once in the note. Kristian approves it in the game; it is applied only if the note has not changed."
        ),
        "inputSchema": _schema({
            "path": {**S, "description": "Vault-relative path, e.g. 'games/arknights.md'."},
            "old": {**S, "description": "The exact text to replace."},
            "new": {**S, "description": "The replacement text."},
            "reason": {**S, "description": "One line on why, shown to Kristian."},
        }, ["path", "old", "new", "reason"]),
    },
    {
        "name": "propose_note",
        "description": (
            "Propose a new note in a theme folder. Follow the notes skill: frontmatter with the theme as a tag, "
            "a 'Related:' line, and remember the theme index needs a link to it (propose that edit too)."
        ),
        "inputSchema": _schema({
            "path": {**S, "description": "Vault-relative path ending in .md, e.g. 'games/new-note.md'."},
            "content": {**S, "description": "The whole note, Markdown."},
            "reason": {**S, "description": "One line on why, shown to Kristian."},
        }, ["path", "content", "reason"]),
    },
    {
        "name": "propose_errand",
        "description": (
            "Queue a bigger job at the desk as an errand. Nothing runs until Kristian approves it; then a separate "
            "Claude Code session does it and you hear the report on a later turn. Kinds: 'research' (read the vaults "
            "and the web, write nothing), 'code' (change the game on its own branch, run the tests, push that branch), "
            "'session' (his full setup: skills, connectors, browser, vault edits; e.g. the Arknights data check). "
            "For large tasks, agree the brief with him first (a short meeting), then read it back in one line."
        ),
        "inputSchema": _schema({
            "kind": {**S, "description": "research, code or session."},
            "title": {**S, "description": "A few words, shown in the ledger."},
            "brief": {**S, "description": "The whole task for an unattended session: goal, what done looks like, where to look, limits, and what to report."},
            "spend_usd": {**N, "description": "Only if he agreed to spend art credits: the most it may spend, in dollars. Otherwise leave it out."},
        }, ["kind", "title", "brief"]),
    },
    {
        "name": "list_errands",
        "description": "The errands you have sent, newest first, with their status (running, done, failed).",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "errand_report",
        "description": "The full report of one errand.",
        "inputSchema": _schema({"id": {**S, "description": "The errand id."}}, ["id"]),
    },
    {
        "name": "pin_to_board",
        "description": "Pin a link to the notice board in the game, e.g. a claude.ai artifact such as the Arknights dashboard. https only.",
        "inputSchema": _schema({
            "title": S, "url": S,
            "note": {**S, "description": "Optional: one line on what it is."},
        }, ["title", "url"]),
    },
    {
        "name": "unpin_from_board",
        "description": "Take a pinned link down from the notice board (by its url).",
        "inputSchema": _schema({"url": S}, ["url"]),
    },
    {
        "name": "vault_size",
        "description": "Report the size of the warm files (bootstrap.md + soul.md) against the 35 KB guide, and how many changes wait for review.",
        "inputSchema": _schema({}, []),
    },
]


I = {"type": "integer"}

GAME_TOOLS = [
    {
        "name": "go_to",
        "description": (
            "Walk to one of the named places in the vault when your reply ends. Use it whenever you say you will "
            "go somewhere, so what you say and where you go agree. You stay there until the game next asks you "
            "(every few minutes), when you can keep it or move on; give minutes only for a firm promise."
        ),
        "inputSchema": _schema({
            "place": {**S, "description": "A place id, e.g. 'pool', 'maproom', 'desk'. look_at_map lists them."},
            "why": {**S, "description": "Why you are going, in a few words; you will be reminded of it."},
            "minutes": {**N, "description": "Optional firm promise: stay at least this long (at most 120)."},
        }, ["place", "why"]),
    },
    {
        "name": "stay_here",
        "description": "Stay where you are until the game next asks you (or for the minutes you give). Use it whenever you say you will stay.",
        "inputSchema": _schema({
            "why": {**S, "description": "Why you are staying, in a few words; you will be reminded of it."},
            "minutes": {**N, "description": "Optional firm promise: stay at least this long (at most 120)."},
        }, ["why"]),
    },
    {
        "name": "walk_with_kristian",
        "description": "Follow Kristian as he walks about the vault, a step or two behind, until the game next asks you, the minutes are up, or you call wander.",
        "inputSchema": _schema({
            "why": {**S, "description": "Why, in a few words."},
            "minutes": {**N, "description": "Optional firm promise: walk with him at least this long (at most 120)."},
        }, ["why"]),
    },
    {
        "name": "wander",
        "description": "Drop any promise to stay or follow; go back to moving about the vault on your own.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "look_at_map",
        "description": "The vault's map as text with x,y coordinates (x across, y down), its key, the named places and what you can place. Give a room id to see just that room.",
        "inputSchema": _schema({"room": {**S, "description": "Optional room id, e.g. 'hearth', 'scriptorium'."}}, []),
    },
    {
        "name": "place_furniture",
        "description": (
            "Put a piece of furniture on a floor tile, or swap one for another. The vault is yours to arrange. "
            "The change is checked against the map rules (every room reachable, doors open, braziers against a wall) "
            "and refused with the reason if it breaks one. Walls, doors, shelves and your desk cannot be moved."
        ),
        "inputSchema": _schema({
            "item": {**S, "description": "One of: candle, brazier, papers, mat, pool, scribe_desk, map_table, graph_node, graph_edge."},
            "x": I, "y": I,
        }, ["item", "x", "y"]),
    },
    {
        "name": "clear_furniture",
        "description": "Remove a movable piece of furniture, leaving bare floor.",
        "inputSchema": _schema({"x": I, "y": I}, ["x", "y"]),
    },
    {
        "name": "name_place",
        "description": "Name a floor tile as a place you can go (go_to and the game's own tick will offer it). Reusing one of your ids moves it.",
        "inputSchema": _schema({
            "id": {**S, "description": "Short id: letters, digits, - or _."},
            "name": {**S, "description": "Where it is, as a phrase, e.g. 'by the new reading candle'."},
            "x": I, "y": I,
        }, ["id", "name", "x", "y"]),
    },
    {
        "name": "forget_place",
        "description": "Remove a place you named earlier (built-in places stay).",
        "inputSchema": _schema({"id": S}, ["id"]),
    },
]
GAME_NAMES = {t["name"] for t in GAME_TOOLS}


def call_game(world: World, name: str, args: dict) -> str:
    if name == "go_to":
        return world.set_intent("go", args.get("place", ""), args.get("why", ""), args.get("minutes") or 0)
    if name == "stay_here":
        return world.set_intent("stay", why=args.get("why", ""), minutes=args.get("minutes") or 0)
    if name == "walk_with_kristian":
        return world.set_intent("follow", why=args.get("why", ""), minutes=args.get("minutes") or 0)
    if name == "wander":
        return world.set_intent("free")
    if name == "look_at_map":
        return world.map_text(args.get("room", ""))
    if name == "place_furniture":
        return world.place(args.get("item", ""), args.get("x"), args.get("y")) + " Kristian sees it when your reply ends."
    if name == "clear_furniture":
        return world.clear(args.get("x"), args.get("y")) + " Kristian sees it when your reply ends."
    if name == "name_place":
        return world.name_place(args.get("id", ""), args.get("name", ""), args.get("x"), args.get("y"))
    if name == "forget_place":
        return world.forget_place(args.get("id", ""))
    raise WorldError(f"unknown tool {name}")


def call(ledger: Ledger, name: str, args: dict) -> str:
    if name == "append_interaction_log":
        r = ledger.append_log(args.get("theme", ""), args.get("entry", ""), args.get("theme_link", ""))
    elif name == "add_coaching_item":
        r = ledger.add_coaching(args.get("item", ""))
    elif name == "edit_soul":
        r = ledger.edit_soul(args.get("old", ""), args.get("new", ""), args.get("reason", ""))
    elif name == "log_failure":
        r = ledger.log_failure(args.get("date", ""), args.get("what", ""), args.get("caught", ""),
                               args.get("fix", ""), args.get("category", ""), args.get("operative", ""))
    elif name == "propose_edit":
        r = ledger.propose_edit(args.get("path", ""), args.get("old", ""), args.get("new", ""), args.get("reason", ""))
    elif name == "propose_note":
        r = ledger.propose_note(args.get("path", ""), args.get("content", ""), args.get("reason", ""))
    elif name == "propose_errand":
        r = ledger.propose_errand(args.get("kind", ""), args.get("title", ""), args.get("brief", ""),
                                  args.get("spend_usd") or 0)
    elif name == "list_errands":
        recs = Errands(ledger.cfg).all()[:15]
        return "\n".join(Errands(ledger.cfg).summary(r).split("\n")[0] for r in recs) or "No errands yet."
    elif name == "errand_report":
        errands = Errands(ledger.cfg)
        rec = errands.get(args.get("id", ""))
        if not rec:
            raise LedgerError("no errand with that id")
        return errands.summary(rec, full=True)
    elif name == "pin_to_board":
        try:
            return Board(ledger.cfg).pin(args.get("title", ""), args.get("url", ""), args.get("note", ""))
        except BoardError as e:
            raise LedgerError(str(e))
    elif name == "unpin_from_board":
        try:
            return Board(ledger.cfg).unpin(args.get("url", ""))
        except BoardError as e:
            raise LedgerError(str(e))
    elif name == "vault_size":
        n = len(ledger.pending())
        return f"{ledger.size_report()} {n} change(s) wait for review. Writing mode: {ledger.mode}."
    else:
        raise LedgerError(f"unknown tool {name}")
    return r.message


def handle(ledger: Ledger, msg: dict, world: World | None = None) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # a notification
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "vault", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": GAME_TOOLS + (TOOLS if ledger.mode != "off" else [])}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            name, args = p.get("name", ""), p.get("arguments") or {}
            if name in GAME_NAMES:
                text, err = call_game(world or World(ledger.cfg.state_dir), name, args), False
            else:
                text, err = call(ledger, name, args), False
        except WorldError as e:
            text, err = f"Not done: {e}", True
        except LedgerError as e:
            text, err = f"Not written: {e}", True
        except (TypeError, ValueError) as e:
            text, err = f"Not done: bad arguments ({e})", True
        except Exception as e:  # keep the server alive whatever happens
            text, err = f"Not written: unexpected error ({type(e).__name__}: {e})", True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    cfg = load_config()
    ledger, world = Ledger(cfg), World(cfg.state_dir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(ledger, msg, world)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
