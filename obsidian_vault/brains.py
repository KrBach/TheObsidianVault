"""Tally's brain: something that turns Kristian's words into a streamed reply.

Every brain yields small event dicts the browser understands:
  {"type": "delta",  "text": "..."}   part of Tally's reply
  {"type": "action", "text": "..."}   something Tally does (e.g. reads a note)
  {"type": "error",  "text": "..."}
  {"type": "done"}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from typing import Iterator

from .config import REPO_ROOT, Config
from .persona import Persona

TOOL_FLAVOUR = {
    "Read": "Tally pulls a note from a shelf",
    "Glob": "Tally runs a finger along the shelf labels",
    "Grep": "Tally leafs through the ledger, looking for something",
    "mcp__vault__append_interaction_log": "Tally writes a line in the ledger",
    "mcp__vault__add_coaching_item": "Tally pins a note for the coaching thread",
    "mcp__vault__propose_edit": "Tally drafts a correction and sets it on the desk for you",
    "mcp__vault__propose_note": "Tally drafts a new note and sets it on the desk for you",
    "mcp__vault__vault_size": "Tally weighs the ledger",
    "mcp__vault__edit_soul": "Tally amends a page of their own",
    "mcp__vault__log_failure": "Tally writes a mistake into the failure log",
    "mcp__vault__propose_errand": "Tally writes out an errand and sets it on the desk for you",
    "mcp__vault__list_errands": "Tally checks the errand book",
    "mcp__vault__errand_report": "Tally reads an errand's report",
    "mcp__vault__pin_to_board": "Tally pins something to the notice board",
    "mcp__vault__unpin_from_board": "Tally takes a page down from the notice board",
    "WebSearch": "Tally sends a runner out for news",
    "WebFetch": "Tally sends for a page from outside the vault",
    "mcp__vault__go_to": "Tally marks a place on the map",
    "mcp__vault__stay_here": "Tally settles in",
    "mcp__vault__walk_with_kristian": "Tally tucks the ledger under one arm, ready to walk",
    "mcp__vault__wander": "Tally shrugs off the last promise",
    "mcp__vault__settle": "Tally looks for somewhere to settle",
    "mcp__vault__look_at_map": "Tally unrolls the plan of the vault",
    "mcp__vault__place_furniture": "Tally rearranges the room",
    "mcp__vault__clear_furniture": "Tally clears a space",
    "mcp__vault__name_place": "Tally pencils a new name onto the plan",
    "mcp__vault__forget_place": "Tally rubs out a name on the plan",
}

VAULT_TOOLS = [
    "mcp__vault__append_interaction_log",
    "mcp__vault__add_coaching_item",
    "mcp__vault__propose_edit",
    "mcp__vault__propose_note",
    "mcp__vault__vault_size",
    "mcp__vault__edit_soul",
    "mcp__vault__log_failure",
    "mcp__vault__propose_errand",
    "mcp__vault__list_errands",
    "mcp__vault__errand_report",
    "mcp__vault__pin_to_board",
    "mcp__vault__unpin_from_board",
]

WEB_TOOLS = ["WebSearch", "WebFetch"]

GAME_TOOLS = [
    "mcp__vault__go_to",
    "mcp__vault__stay_here",
    "mcp__vault__walk_with_kristian",
    "mcp__vault__wander",
    "mcp__vault__settle",
    "mcp__vault__look_at_map",
    "mcp__vault__place_furniture",
    "mcp__vault__clear_furniture",
    "mcp__vault__name_place",
    "mcp__vault__forget_place",
]


def write_mcp_config(cfg: Config) -> str:
    """The MCP config that lets Claude Code start the vault tools. Returns its path."""
    env = {
        "PYTHONPATH": str(REPO_ROOT),
        "VAULT_WRITES": cfg.writes_mode,
        "VAULT_STATE_DIR": str(cfg.state_dir),
    }
    if cfg.config_path:
        env["VAULT_CONFIG"] = str(cfg.config_path)
    if cfg.core_dir:
        env["TALLY_CORE_DIR"] = str(cfg.core_dir)
    conf = {"mcpServers": {"vault": {"type": "stdio", "command": sys.executable,
                                     "args": ["-m", "obsidian_vault.vault_mcp"], "env": env}}}
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.state_dir / "mcp.json"
    path.write_text(json.dumps(conf, indent=2), encoding="utf-8")
    return str(path)


CLAUDE_MISSING = (
    "Could not find the Claude Code command (`claude`). In a terminal, run `which claude` "
    "and put that path in config.toml under [brain] as claude_path = \"/path/to/claude\". "
    "If `which claude` prints nothing, install Claude Code first."
)


class Brain:
    name = "brain"

    def talk(self, text: str) -> Iterator[dict]:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def muse(self, system: str, prompt: str, model: str) -> str:
        """One short, separate call with no tools and no memory (Tally's tick). Raises on failure."""
        raise NotImplementedError


class ClaudeCodeBrain(Brain):
    """Runs `claude -p` in headless mode and resumes the same session each turn."""

    name = "claude-code"

    def __init__(self, cfg: Config, persona: Persona):
        self.cfg = cfg
        self.persona = persona
        self.lock = threading.Lock()
        self.exe = cfg.claude_path
        self.writes = cfg.writes_mode != "off"
        self.mcp_config = write_mcp_config(cfg)  # the game tools are always there; writing tools by mode
        # The conversation survives a restart of the game: the session id is kept in the state folder.
        self.session_file = cfg.state_dir / "session.json"
        self.session_id: str | None = self._load_session()

    def _load_session(self) -> str | None:
        try:
            return json.loads(self.session_file.read_text(encoding="utf-8")).get("session_id") or None
        except (OSError, ValueError):
            return None

    def _save_session(self) -> None:
        try:
            if self.session_id:
                self.session_file.write_text(json.dumps({"session_id": self.session_id}), encoding="utf-8")
            elif self.session_file.exists():
                self.session_file.unlink()
        except OSError:
            pass

    def reset(self) -> None:
        self.session_id = None
        self._save_session()

    def _tools(self) -> list[str]:
        tools = list(self.cfg.tools)
        if self.cfg.web:
            tools += [t for t in WEB_TOOLS if t not in tools]
        return tools

    def _command(self) -> list[str]:
        tools = ",".join(self._tools())
        allowed = ",".join(self._tools() + GAME_TOOLS + (VAULT_TOOLS if self.writes else []))
        cmd = [
            self.exe or "claude", "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", self.persona.system_prompt,
            # Built-in tools stay read-only; writing goes only through the vault tools.
            "--tools", tools,
            "--allowedTools", allowed,
            # Anything not on the allow list is refused instead of prompting.
            "--permission-mode", "dontAsk",
        ]
        # Only our vault server: no MCP servers from the user's own Claude Code setup.
        cmd += ["--mcp-config", self.mcp_config, "--strict-mcp-config"]
        if self.cfg.work_dir and self.cfg.work_dir.is_dir():
            cmd += ["--add-dir", str(self.cfg.work_dir)]  # read-only: only Read/Glob/Grep can reach it
        if self.cfg.model:
            cmd += ["--model", self.cfg.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def talk(self, text: str) -> Iterator[dict]:
        if not self.exe:
            yield {"type": "error", "text": CLAUDE_MISSING}
            return
        cwd = self.cfg.core_dir if self.cfg.core_dir and self.cfg.core_dir.is_dir() else None
        with self.lock:
            proc = subprocess.Popen(
                self._command(), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
            )
            assert proc.stdin and proc.stdout
            proc.stdin.write(text)
            proc.stdin.close()

            got_text = False
            resumed = self.session_id
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("session_id"):
                    self.session_id = msg["session_id"]
                if msg.get("type") != "stream_event":
                    if msg.get("type") == "result" and msg.get("is_error"):
                        yield {"type": "error", "text": str(msg.get("result") or "Claude Code reported an error.")}
                    continue
                ev = msg.get("event", {})
                if msg.get("parent_tool_use_id"):
                    continue  # ignore any nested agent output
                if ev.get("type") == "content_block_start":
                    block = ev.get("content_block", {})
                    if block.get("type") == "tool_use":
                        yield {"type": "action", "text": TOOL_FLAVOUR.get(block.get("name"), f"Tally uses {block.get('name')}")}
                    elif block.get("type") == "text" and got_text:
                        yield {"type": "delta", "text": "\n\n"}
                elif ev.get("type") == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta":
                        got_text = True
                        yield {"type": "delta", "text": delta.get("text", "")}

            proc.wait()
            if proc.returncode != 0 and not got_text:
                err = (proc.stderr.read() if proc.stderr else "").strip()
                if resumed and self.session_id == resumed:
                    # Most often the saved conversation is gone (cleaned up, or another machine).
                    self.session_id = None
                    err = (err[-300:] + "\n" if err else "") + "Could not pick up the last conversation; the next message starts a fresh one."
                yield {"type": "error", "text": err[-500:] or f"claude exited with code {proc.returncode}"}
            self._save_session()
        yield {"type": "done"}

    def muse(self, system: str, prompt: str, model: str) -> str:
        if not self.exe:
            raise RuntimeError(CLAUDE_MISSING)
        base = [self.exe, "-p", "--output-format", "json", "--system-prompt", system,
                "--tools", "", "--strict-mcp-config", "--permission-mode", "dontAsk"]
        if model:
            base += ["--model", model]
        # Run outside the vault and the repo so no CLAUDE.md is loaded: the tick stays small.
        cwd = tempfile.gettempdir()
        for extra in (["--no-session-persistence"], []):  # older Claude Code lacks the flag
            proc = subprocess.run(base + extra, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", cwd=cwd, timeout=120)
            if proc.returncode == 0 or "no-session-persistence" not in (proc.stderr or ""):
                break
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError((proc.stderr or proc.stdout or "no output").strip()[-300:])
        if out.get("is_error"):
            raise RuntimeError(str(out.get("result") or "Claude Code reported an error."))
        return str(out.get("result") or "")


class ApiBrain(Brain):
    """Calls the Claude API directly. Keeps the conversation in memory."""

    name = "api"
    DEFAULT_MODEL = "claude-opus-5-5"

    def __init__(self, cfg: Config, persona: Persona):
        try:
            import anthropic  # imported here so claude-code mode needs no packages
        except ModuleNotFoundError:
            raise SystemExit(
                'brain.kind is "api", which needs the anthropic package: '
                "python3 -m pip install -r requirements.txt\n"
                'To use your Claude Code login instead, set kind = "claude-code" in config.toml.'
            )

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = cfg.model or self.DEFAULT_MODEL
        self.persona = persona
        self.history: list = []
        self.lock = threading.Lock()

    def reset(self) -> None:
        self.history = []

    def talk(self, text: str) -> Iterator[dict]:
        with self.lock:
            self.history.append({"role": "user", "content": text})
            try:
                with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    # The core is large and stable: cache it so each turn is cheap.
                    system=[{"type": "text", "text": self.persona.system_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=self.history,
                    output_config={"effort": "medium"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    for chunk in stream.text_stream:
                        yield {"type": "delta", "text": chunk}
                    final = stream.get_final_message()
            except self.anthropic.APIStatusError as e:
                self.history.pop()
                yield {"type": "error", "text": f"API error {e.status_code}: {e.message}"}
                yield {"type": "done"}
                return
            except self.anthropic.APIConnectionError:
                self.history.pop()
                yield {"type": "error", "text": "Could not reach the Claude API."}
                yield {"type": "done"}
                return

            if final.stop_reason == "refusal":
                self.history.pop()
                yield {"type": "error", "text": "Tally declined to answer that one."}
            else:
                self.history.append({"role": "assistant", "content": final.content})
        yield {"type": "done"}

    MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}

    def muse(self, system: str, prompt: str, model: str) -> str:
        msg = self.client.messages.create(
            model=self.MODELS.get(model, model or self.model), max_tokens=200,
            system=system, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")


def make_brain(cfg: Config, persona: Persona) -> Brain:
    if cfg.brain == "api":
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            print("note: ANTHROPIC_API_KEY is not set; the SDK will try other credentials (e.g. `ant auth login`).")
        return ApiBrain(cfg, persona)
    return ClaudeCodeBrain(cfg, persona)
