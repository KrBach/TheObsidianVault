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
import threading
from typing import Iterator

from .config import Config
from .persona import Persona

TOOL_FLAVOUR = {
    "Read": "Tally pulls a note from a shelf",
    "Glob": "Tally runs a finger along the shelf labels",
    "Grep": "Tally leafs through the ledger, looking for something",
}


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


class ClaudeCodeBrain(Brain):
    """Runs `claude -p` in headless mode and resumes the same session each turn."""

    name = "claude-code"

    def __init__(self, cfg: Config, persona: Persona):
        self.cfg = cfg
        self.persona = persona
        self.session_id: str | None = None
        self.lock = threading.Lock()
        self.exe = cfg.claude_path

    def reset(self) -> None:
        self.session_id = None

    def _command(self) -> list[str]:
        tools = ",".join(self.cfg.tools)
        cmd = [
            self.exe or "claude", "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt", self.persona.system_prompt,
            "--tools", tools,
            "--allowedTools", tools,
            # Anything not on the allow list is refused instead of prompting.
            "--permission-mode", "dontAsk",
        ]
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
                yield {"type": "error", "text": err[-500:] or f"claude exited with code {proc.returncode}"}
        yield {"type": "done"}


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


def make_brain(cfg: Config, persona: Persona) -> Brain:
    if cfg.brain == "api":
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            print("note: ANTHROPIC_API_KEY is not set; the SDK will try other credentials (e.g. `ant auth login`).")
        return ApiBrain(cfg, persona)
    return ClaudeCodeBrain(cfg, persona)
