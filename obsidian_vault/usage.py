"""The usage meter: what Claude Code has used lately, as an estimate at API prices.

Claude Code writes one JSONL log per session under ~/.claude/projects (or $CLAUDE_CONFIG_DIR/projects).
This module reads only the bookkeeping fields of each line: the timestamp, the model, the token counts
and the message and request ids (to count a reply once). It never reads, keeps or prints the content
of a message, and it never prints a log's path.

Two numbers come out of it, both estimates at API prices, not the plan's own accounting:
- the current 5-hour block: it starts at the hour of its first message (rounded down) and lasts five
  hours; a message five hours or more after the block started, or after a gap of five hours, starts a
  new one (as ccusage counts them);
- today: everything since local midnight, in the game's time zone.

Errands use it too: the tokens of one run, priced the same way, and a projection per kind.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

BLOCK_SECONDS = 5 * 3600
# Dollars per million tokens: input, output, cache read, cache write. Opus 5.5 is from the
# 2026-11-26 usage report (cache write at twice the input price). A model not in the table is priced
# as Opus, so the estimate errs high, never low.
PRICES = {
    "opus": (4.00, 20.00, 0.20, 8.00),
}
DEFAULT_PRICE = PRICES["opus"]
ESTIMATE_NOTE = "an estimate from API prices, not the plan's accounting"
CACHE_SECONDS = 30  # how long a reading of the logs is reused before they are read again


def default_logs_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(os.path.expanduser(base)) if base else Path.home() / ".claude") / "projects"


def price_for(model: str) -> tuple[float, float, float, float]:
    m = (model or "").lower()
    for family, price in PRICES.items():
        if family in m:
            return price
    return DEFAULT_PRICE


def cost(tokens: dict, model: str = "") -> float:
    """Dollars for one set of token counts at the model's API price."""
    p_in, p_out, p_read, p_write = price_for(model)
    return (tokens.get("input", 0) * p_in + tokens.get("output", 0) * p_out
            + tokens.get("cache_read", 0) * p_read + tokens.get("cache_write", 0) * p_write) / 1_000_000


def total_tokens(tokens: dict) -> int:
    return int(sum(tokens.get(k, 0) for k in ("input", "output", "cache_read", "cache_write")))


def _parse_time(raw) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tokens(usage: dict) -> dict:
    def n(key):
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    return {"input": n("input_tokens"), "output": n("output_tokens"),
            "cache_read": n("cache_read_input_tokens"), "cache_write": n("cache_creation_input_tokens")}


def read_entries(logs_dir: Path, since: float = 0.0, session: str = "") -> list[dict]:
    """Every priced reply in the logs at or after `since` (a Unix time), oldest first.

    Each entry: {"t": time, "model": ..., "tokens": {...}, "usd": ..., "session": ...}. Only the
    bookkeeping fields are read. A reply that appears in more than one line counts once. With
    `session`, only that session's replies.
    """
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return []
    seen: set[str] = set()
    out = []
    for f in logs_dir.rglob("*.jsonl"):
        try:
            if since and f.stat().st_mtime < since:
                continue  # nothing in a log last written before `since` can be newer than it
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    msg = rec.get("message") if isinstance(rec, dict) else None
                    if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                        continue
                    if session and rec.get("sessionId") != session:
                        continue
                    t = _parse_time(rec.get("timestamp"))
                    if t is None or t < since:
                        continue
                    key = f"{msg.get('id', '')}:{rec.get('requestId', '')}"
                    if key != ":":
                        if key in seen:
                            continue
                        seen.add(key)
                    model = str(msg.get("model") or "")
                    if model == "<synthetic>":
                        continue
                    tokens = _tokens(msg["usage"])
                    out.append({"t": t, "model": model, "tokens": tokens, "usd": cost(tokens, model),
                                "session": str(rec.get("sessionId") or "")})
        except OSError:
            continue
    out.sort(key=lambda e: e["t"])
    return out


def blocks(entries: list[dict]) -> list[dict]:
    """5-hour blocks from entries sorted oldest first: [{"start", "end", "last", "tokens", "usd"}]."""
    out: list[dict] = []
    for e in entries:
        b = out[-1] if out else None
        if b is None or e["t"] >= b["end"] or e["t"] - b["last"] >= BLOCK_SECONDS:
            start = e["t"] - (e["t"] % 3600)  # rounded down to the hour (UTC hours; whole-hour zones agree)
            b = {"start": start, "end": start + BLOCK_SECONDS, "last": e["t"], "tokens": 0, "usd": 0.0}
            out.append(b)
        b["last"] = e["t"]
        b["tokens"] += total_tokens(e["tokens"])
        b["usd"] += e["usd"]
    return out


def current_block(entries: list[dict], now: float) -> dict | None:
    """The block still open at `now`, or None (then the next message starts a fresh one)."""
    bs = blocks(entries)
    if not bs:
        return None
    b = bs[-1]
    if now < b["end"] and now - b["last"] < BLOCK_SECONDS:
        return b
    return None


def _zone(tz: str):
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz)
        except Exception:  # noqa: BLE001 - an unknown zone falls back to this computer's
            pass
    return None


def local_midnight(now: float, tz: str = "") -> float:
    zone = _zone(tz)
    dt = datetime.fromtimestamp(now, zone) if zone else datetime.fromtimestamp(now).astimezone()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def clock(t: float, tz: str = "") -> str:
    zone = _zone(tz)
    dt = datetime.fromtimestamp(t, zone) if zone else datetime.fromtimestamp(t).astimezone()
    return dt.strftime("%H:%M")


class Meter:
    """Reads the logs (at most every CACHE_SECONDS) and says where the block and the day stand."""

    def __init__(self, cfg, clock_fn=time.time):
        self.cfg = cfg
        self.clock = clock_fn
        self.lock = threading.Lock()
        self._cache: tuple[float, dict] | None = None

    @property
    def logs_dir(self) -> Path:
        return Path(getattr(self.cfg, "usage_logs_dir", None) or default_logs_dir())

    def reading(self, fresh: bool = False) -> dict:
        now = self.clock()
        with self.lock:
            if not fresh and self._cache and now - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
            tz = getattr(self.cfg, "timezone", "") or ""
            midnight = local_midnight(now, tz)
            # far enough back to see the start of a block that is still open, and all of today
            since = min(midnight, now - 2 * BLOCK_SECONDS)
            readable = self.logs_dir.is_dir()
            entries = read_entries(self.logs_dir, since) if readable else []
            block = current_block(entries, now)
            today = sum(e["usd"] for e in entries if e["t"] >= midnight)
            budget = float(getattr(self.cfg, "usage_block_budget", 6.0))
            cap = float(getattr(self.cfg, "usage_daily_cap", 12.0))
            r = {
                "readable": readable,
                "now": now,
                "block": ({"start": block["start"], "reset": block["end"], "tokens": block["tokens"],
                           "usd": round(block["usd"], 4)} if block else None),
                "block_usd": round(block["usd"], 4) if block else 0.0,
                "today_usd": round(today, 4),
                "block_budget": budget,
                "daily_cap": cap,
                "timezone": tz,
            }
            self._cache = (now, r)
            return r

    def line(self, r: dict | None = None) -> str:
        """One line for Tally's game_state."""
        r = r or self.reading()
        if not r["readable"]:
            return "Usage: unknown (Claude Code's local logs were not found)."
        tz = r.get("timezone", "")
        budget, cap = r["block_budget"], r["daily_cap"]
        if r["block"]:
            pct = round(100 * r["block_usd"] / budget) if budget > 0 else 0
            block = f"block {pct}% of budget (${r['block_usd']:.2f} of ${budget:g}), resets {clock(r['block']['reset'], tz)}"
        else:
            block = "no block open (the next message starts one)"
        return f"Usage: {block}; today ${r['today_usd']:.2f} of ${cap:g}. This is {ESTIMATE_NOTE}."

    def room_for(self, projected: float, r: dict | None = None) -> tuple[bool, str]:
        """Whether a job projected to cost `projected` dollars fits the block budget and the daily cap."""
        r = r or self.reading()
        if not r["readable"]:
            return False, "usage cannot be read (Claude Code's local logs were not found)"
        if r["block_usd"] + projected > r["block_budget"]:
            when = f", resets {clock(r['block']['reset'], r.get('timezone', ''))}" if r["block"] else ""
            return False, (f"the block has ${max(0.0, r['block_budget'] - r['block_usd']):.2f} left of "
                           f"${r['block_budget']:g} and this needs about ${projected:.2f}{when}")
        if r["today_usd"] + projected > r["daily_cap"]:
            return False, (f"today has ${max(0.0, r['daily_cap'] - r['today_usd']):.2f} left of "
                           f"${r['daily_cap']:g} and this needs about ${projected:.2f}")
        return True, ""


def tokens_from_result(data: dict) -> tuple[dict, str] | None:
    """Token counts from `claude -p --output-format json`: the per-model totals if there, else `usage`.

    Returns (tokens, model) where model is the one with the most output, or None if nothing is there.
    """
    if not isinstance(data, dict):
        return None
    mu = data.get("modelUsage")
    if isinstance(mu, dict) and mu:
        tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        usd, top, top_out = 0.0, "", -1
        for model, u in mu.items():
            if not isinstance(u, dict):
                continue
            t = {"input": int(u.get("inputTokens") or 0), "output": int(u.get("outputTokens") or 0),
                 "cache_read": int(u.get("cacheReadInputTokens") or 0),
                 "cache_write": int(u.get("cacheCreationInputTokens") or 0)}
            for k in tokens:
                tokens[k] += t[k]
            usd += cost(t, model)
            if t["output"] > top_out:
                top, top_out = model, t["output"]
        tokens["usd"] = round(usd, 4)
        return tokens, top
    if isinstance(data.get("usage"), dict):
        t = _tokens(data["usage"])
        model = str(data.get("model") or "")
        t["usd"] = round(cost(t, model), 4)
        return t, model
    return None


def session_tokens(logs_dir: Path, session: str, since: float = 0.0) -> dict | None:
    """The tokens and estimated dollars of one Claude Code session, from its logs."""
    if not session:
        return None
    entries = read_entries(logs_dir, since, session=session)
    if not entries:
        return None
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for e in entries:
        for k in tokens:
            tokens[k] += e["tokens"][k]
    tokens["usd"] = round(sum(e["usd"] for e in entries), 4)
    return tokens
