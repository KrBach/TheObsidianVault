"""A tiny MCP server (stdio, no packages) that gives Tally the vault-writing tools.

Claude Code starts it with `python3 -m obsidian_vault.vault_mcp` when writes.mode is not
"off". Every tool goes through the Ledger, which enforces the rules in ledger.py.
"""

from __future__ import annotations

import json
import sys

from .config import load_config
from .ledger import Ledger, LedgerError

PROTOCOL = "2025-06-18"


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}

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
        "name": "vault_size",
        "description": "Report the size of the warm files (bootstrap.md + soul.md) against the 35 KB guide, and how many changes wait for review.",
        "inputSchema": _schema({}, []),
    },
]


def call(ledger: Ledger, name: str, args: dict) -> str:
    if name == "append_interaction_log":
        r = ledger.append_log(args.get("theme", ""), args.get("entry", ""), args.get("theme_link", ""))
    elif name == "add_coaching_item":
        r = ledger.add_coaching(args.get("item", ""))
    elif name == "propose_edit":
        r = ledger.propose_edit(args.get("path", ""), args.get("old", ""), args.get("new", ""), args.get("reason", ""))
    elif name == "propose_note":
        r = ledger.propose_note(args.get("path", ""), args.get("content", ""), args.get("reason", ""))
    elif name == "vault_size":
        n = len(ledger.pending())
        return f"{ledger.size_report()} {n} change(s) wait for review. Writing mode: {ledger.mode}."
    else:
        raise LedgerError(f"unknown tool {name}")
    return r.message


def handle(ledger: Ledger, msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # a notification
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "vault", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            text, err = call(ledger, p.get("name", ""), p.get("arguments") or {}), False
        except LedgerError as e:
            text, err = f"Not written: {e}", True
        except Exception as e:  # keep the server alive whatever happens
            text, err = f"Not written: unexpected error ({type(e).__name__}: {e})", True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    ledger = Ledger(load_config())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(ledger, msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
