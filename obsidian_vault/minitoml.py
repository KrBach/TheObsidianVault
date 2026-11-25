"""Reads config.toml on any Python 3.9+, and edits single values in it without losing comments.

Uses the standard tomllib on 3.11+, or tomli if installed. Otherwise falls back to a
tiny reader that covers what config.toml needs: [sections], strings, numbers,
true/false and one-line lists, with # comments.
"""

from __future__ import annotations

import ast
import json
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


def loads(text: str) -> dict:
    if _toml is not None:
        return _toml.loads(text)
    return _loads_minimal(text)


def dump_value(value) -> str:
    """One value as TOML: a string, a bool, a number or a one-line list of those."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(dump_value(v) for v in value) + "]"
    # JSON's string escapes (\" \\ \n \uXXXX) are all valid in a TOML basic string
    return json.dumps(str(value), ensure_ascii=False)


_HEADER = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$")


def set_values(text: str, section: str, values: dict) -> str:
    """Set keys in one [section] of a TOML text, keeping every comment and every other line.

    An existing `key = ...` line in the section is replaced in place; a missing key goes after the
    section's last key; a missing section is added at the end. Only for the flat, one-line values
    config.toml uses.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if (m := _HEADER.match(ln)) and m.group(1) == section), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines += [f"{k} = {dump_value(v)}" for k, v in values.items()]
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if _HEADER.match(lines[i])), len(lines))
    last_key = start
    todo = dict(values)
    for i in range(start + 1, end):
        m = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", lines[i])
        if not m:
            continue
        last_key = i
        if m.group(1) in todo:
            lines[i] = f"{m.group(1)} = {dump_value(todo.pop(m.group(1)))}"
    for offset, (k, v) in enumerate(todo.items(), 1):
        lines.insert(last_key + offset, f"{k} = {dump_value(v)}")
    return "\n".join(lines) + "\n"
