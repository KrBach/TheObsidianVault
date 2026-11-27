"""Dashboards: pages the game builds from a data file in the vault, with plain code and no model.

A dashboard is a fixed HTML template with one placeholder, __DASHBOARD_DATA__, that the game
replaces with the contents of a JSON file. An errand (or OWNER) only keeps the JSON up to date;
the page itself never goes through a model, which is what made a data-check errand slow and
expensive (rewriting a 300 KB page, or reading the whole history JSON into context to do it).

    [dashboards]
    publish = "claude"          # or "off": build the page only
    publish_model = "haiku"     # the small run that hands the built page to the Artifact tool

    [dashboards.depot]          # one table per dashboard; the name is yours
    data = "games/depot-history.json"            # relative to the vault folder (tally.core_dir)
    template = "games/depot-dashboard.template.html"
    output = "Claude outputs/depot-dashboard.html"   # default: Claude outputs/<name>.html
    artifact = "https://claude.ai/artifact/..."      # where the page is published, if anywhere

After every finished errand that is not a code errand, the game builds each dashboard whose data
file changed during the run, puts the page in the vault (the notice board shows it), and, when
`publish` is on and an artifact link is set, starts one small `claude -p` run whose only tool is
Artifact to republish it to that link. The publish step is the only part a model does, and it never
sees the data: it gets a file path and a URL.

By hand: `python3 -m obsidian_vault.dashboards build <name>` (or `publish <name>`, or `list`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = "__DASHBOARD_DATA__"
PUBLISH_TIMEOUT = 300  # seconds for the publish run
# What the publish run answers with, so the game can tell the outcome without reading prose.
PUBLISHED, NOT_PUBLISHED = "PUBLISHED", "NOT PUBLISHED:"


class DashboardError(Exception):
    pass


@dataclass
class Dashboard:
    name: str
    data: Path
    template: Path
    output: Path
    artifact: str = ""
    data_rel: str = ""  # the data path as written in config.toml (relative to the vault folder)


def _inside(root: Path, rel: str) -> Path:
    """A path in the vault folder; refuses one that leads out of it."""
    p = (root / rel).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise DashboardError(f"{rel!r} is outside the vault folder")
    return p


def configured(cfg) -> list[Dashboard]:
    """The dashboards in config.toml, with their paths resolved inside the vault folder. A table
    with a bad path is left out (load_config already noted it)."""
    root = cfg.core_dir
    out = []
    if not root:
        return out
    for name, spec in (cfg.dashboards or {}).items():
        try:
            out.append(Dashboard(
                name=name,
                data=_inside(root, spec["data"]),
                template=_inside(root, spec["template"]),
                output=_inside(root, spec.get("output") or f"Claude outputs/{name}.html"),
                artifact=str(spec.get("artifact", "")).strip(),
                data_rel=str(spec["data"])))
        except (KeyError, TypeError, DashboardError):
            continue
    return out


def find(cfg, name: str) -> Dashboard:
    for d in configured(cfg):
        if d.name == name:
            return d
    names = ", ".join(d.name for d in configured(cfg)) or "none"
    raise DashboardError(f"no dashboard named {name!r} in config.toml (configured: {names})")


def render(template: str, data_text: str) -> str:
    """The page: the template with the JSON in place of the placeholder. The JSON is checked and
    written compactly, with "</" escaped so a string in it can never close the <script>."""
    if PLACEHOLDER not in template:
        raise DashboardError(f"the template has no {PLACEHOLDER} placeholder")
    try:
        data = json.loads(data_text)
    except ValueError as e:
        raise DashboardError(f"the data file is not valid JSON ({e})") from e
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    blob = blob.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template.replace(PLACEHOLDER, blob, 1)


def build(d: Dashboard) -> dict:
    """Write the page next to its siblings in the vault. Returns {name, output, bytes}."""
    try:
        template = d.template.read_text(encoding="utf-8")
        data_text = d.data.read_text(encoding="utf-8")
    except OSError as e:
        raise DashboardError(f"could not read {Path(e.filename or '').name or 'a file'}: {e.strerror}") from e
    page = render(template, data_text)
    d.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = d.output.with_suffix(d.output.suffix + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(d.output)
    return {"name": d.name, "output": str(d.output), "bytes": len(page.encode("utf-8"))}


def changed_since(d: Dashboard, since: float) -> bool:
    try:
        return d.data.stat().st_mtime >= since
    except OSError:
        return False


# ---------- publishing ----------

PUBLISH_PROMPT = """Publish one file to an existing claude.ai Artifact, and do nothing else.

File: {file}
Artifact: {url}

If the Artifact tool is listed only by name (a deferred tool), load it first with ToolSearch, query
"select:Artifact". Then call the Artifact tool with action "publish", file_path set to the file and url set to the artifact.
If that is refused because this session has not read the artifact, the refusal counts as the read:
publish the same file to the same url again, unchanged. Do not read, edit or rewrite the file, and
do not open any other file.

Answer with one line only: "{ok} <the artifact url>" when the publish went through, or
"{no} <the reason, in a few words>" when it did not (for example: no Artifact tool in this run)."""


def publish_command(cfg, d: Dashboard) -> list[str]:
    # In `claude -p` with a narrowed tool list, Artifact can come as a deferred tool: its name is listed
    # but its schema only loads through ToolSearch. Without ToolSearch the run cannot call it at all
    # (it answered "no Artifact tool in this run"), so ToolSearch always comes along.
    tools = ",".join(dict.fromkeys([cfg.dashboard_publish_tool, "ToolSearch"]))
    cmd = [cfg.claude_path or "claude", "-p", "--output-format", "json",
           "--tools", tools, "--allowedTools", tools,
           "--permission-mode", "dontAsk", "--strict-mcp-config"]
    if cfg.dashboard_publish_model:
        cmd += ["--model", cfg.dashboard_publish_model]
    return cmd


def publish(cfg, d: Dashboard) -> dict:
    """Hand the built page to the Artifact tool in a small `claude -p` run. Returns {ok, line, data}:
    ok is True only when the run answered PUBLISHED; data is the run's JSON (for its cost)."""
    if not d.artifact:
        return {"ok": False, "line": "no artifact link is set for it", "data": None}
    if not cfg.claude_path:
        return {"ok": False, "line": "Claude Code was not found", "data": None}
    prompt = PUBLISH_PROMPT.format(file=d.output, url=d.artifact, ok=PUBLISHED, no=NOT_PUBLISHED)
    try:
        proc = subprocess.run(publish_command(cfg, d), input=prompt, capture_output=True, text=True,
                              encoding="utf-8", cwd=str(d.output.parent), timeout=PUBLISH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "line": f"the publish run timed out after {PUBLISH_TIMEOUT // 60} minutes", "data": None}
    except OSError as e:
        return {"ok": False, "line": f"the publish run could not start ({e.strerror or e})", "data": None}
    try:
        data = json.loads(proc.stdout)
        text = str(data.get("result") or "").strip()
    except (ValueError, AttributeError):
        data, text = None, (proc.stdout or proc.stderr or "").strip()
    last = text.splitlines()[-1].strip() if text else ""
    if last.startswith(PUBLISHED) and not (data or {}).get("is_error"):
        return {"ok": True, "line": f"published to {d.artifact}", "data": data}
    reason = last[len(NOT_PUBLISHED):].strip() if last.startswith(NOT_PUBLISHED) else (last or f"exit {proc.returncode}")
    return {"ok": False, "line": reason[:300], "data": data}


def after_errand(cfg, since: float, short=lambda p: p) -> tuple[str, list[dict]]:
    """Build (and publish, when set) every dashboard whose data changed since `since`. Returns a
    report section for the errand ("" when nothing changed) and the publish runs' JSON, for cost."""
    lines, runs = [], []
    for d in configured(cfg):
        if not changed_since(d, since):
            continue
        try:
            built = build(d)
        except (DashboardError, OSError) as e:
            lines.append(f"- {d.name}: its data changed, but the page could not be built: {e}")
            continue
        where = short(built["output"])
        if cfg.dashboard_publish == "claude" and d.artifact:
            res = publish(cfg, d)
            if res["data"]:
                runs.append(res["data"])
            if res["ok"]:
                lines.append(f"- {d.name}: built by the game ({where}) and {res['line']}.")
            else:
                lines.append(f"- {d.name}: built by the game ({where}), on the notice board. NOT published to "
                             f"{d.artifact}: {res['line']}. Any Claude session with the Artifact tool can publish "
                             "that file to that link as it is.")
        elif d.artifact:
            lines.append(f"- {d.name}: built by the game ({where}), on the notice board. Publishing is off "
                         f"([dashboards] publish), so {d.artifact} still shows the last version.")
        else:
            lines.append(f"- {d.name}: built by the game ({where}), on the notice board.")
    if not lines:
        return "", runs
    return "**Dashboards (the game, after the run):**\n" + "\n".join(lines), runs


def main(argv: list[str]) -> int:
    from .config import load_config, short_path
    cfg = load_config()
    if not argv or argv[0] not in ("list", "build", "publish"):
        print("usage: python3 -m obsidian_vault.dashboards list | build <name> | publish <name>")
        return 2
    try:
        if argv[0] == "list":
            for d in configured(cfg):
                print(f"{d.name}: {short_path(d.data)} -> {short_path(d.output)}  {d.artifact or '(not published)'}")
            return 0
        d = find(cfg, argv[1] if len(argv) > 1 else "")
        built = build(d)
        print(f"built {short_path(built['output'])} ({built['bytes'] // 1024} KB)")
        if argv[0] == "publish":
            res = publish(cfg, d)
            print(("" if res["ok"] else "not published: ") + res["line"])
            return 0 if res["ok"] else 1
        return 0
    except DashboardError as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
