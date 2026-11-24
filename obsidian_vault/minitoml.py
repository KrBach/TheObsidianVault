"""Reads config.toml on any Python 3.9+.

Uses the standard tomllib on 3.11+, or tomli if installed. Otherwise falls back to a
tiny reader that covers what config.toml needs: [sections], strings, numbers,
true/false and one-line lists, with # comments.
"""

from __future__ import annotations

import ast
import re

try:
    import tomllib as _toml  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as _toml  # pip install tomli
    except ModuleNotFoundError:
        _toml = None


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).strip()


def _value(raw: str):
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]  # TOML literal string: no escapes
    return ast.literal_eval(raw)  # "strings", numbers, ["lists"]


def _loads_minimal(text: str) -> dict:
    data: dict = {}
    table = data
    for n, line in enumerate(text.splitlines(), 1):
        line = _strip_comment(line)
        if not line:
            continue
        m = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\]", line)
        if m:
            table = data
            for part in m.group(1).split("."):
                table = table.setdefault(part, {})
            continue
        key, sep, raw = line.partition("=")
        if not sep:
            raise ValueError(f"config.toml line {n}: expected key = value")
        try:
            table[key.strip().strip('"')] = _value(raw.strip())
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"config.toml line {n}: cannot read value {raw.strip()!r}") from e
    return data


def load(f) -> dict:
    """Like tomllib.load: takes a file opened in binary mode."""
    if _toml is not None:
        return _toml.load(f)
    return _loads_minimal(f.read().decode("utf-8"))
