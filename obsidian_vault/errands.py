"""Errands: bigger jobs Tally queues in the ledger, run by a separate Claude Code session once approved.

The talk session stays small and read-only. When something needs more (a data check, a change to
the game, research that takes a while), Tally writes a brief with propose_errand. OWNER approves
it at the desk, and only then does the game start `claude -p` for it, with the tools that kind of
errand needs and nothing else. The result comes back as a report Tally hears about on the next turn
and that the notice board shows.

Kinds:
  research  read the vaults and the web, write nothing; the report is the result
  code      change the game in its own git worktree and branch (never the copy that is running),
            run the tests, commit, and push that one branch; never main, never a merge
  session   a full session with OWNER's own Claude Code setup (skills, connectors, browser), in
            the vault folder, for things like a data check with your own skills; tools that spend art
            credits are blocked unless the brief carries a spending limit the owner approved

Records live in <state_dir>/errands/<id>.json; the full output in <id>.log.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .config import BROWSER_APPS, Config
from .persona import load_core

KINDS = ("research", "code", "session")

# Tools that spend the owner's art credits, under the names connectors get in Claude Code.
_SPRITEFORGE = ["generate"]
_RECRAFT = ["generate_image", "image_to_image", "image_edit", "inpaint_image", "variate_image",
            "creative_upscale", "crisp_upscale", "generate_background", "replace_background",
            "remove_background", "vectorize_image", "image_to_video", "erase_region", "call_agent"]
COST_TOOLS = [f"{prefix}{tool}"
              for prefixes, tools in (
                  (("mcp__Spriteforge__", "mcp__spriteforge__", "mcp__claude_ai_Spriteforge__", "mcp__claude_ai_SpriteForge__"), _SPRITEFORGE),
                  (("mcp__recraft__", "mcp__Recraft__", "mcp__claude_ai_Recraft__", "mcp__claude_ai_recraft__"), _RECRAFT))
              for prefix in prefixes for tool in tools]

# The browser a session errand can have. A `claude -p` run the game starts is not inside the Claude
# desktop app, so the app's built-in browser (mcp__Claude_Browser__*, or mcp__remote-devices__... from
# the cloud) never reaches it. Two browsers do:
#   safari  MCPSafari, a local MCP server (`mcp-safari`, in OWNER's ~/.claude.json) bridged to a Safari
#           extension; its tools are mcp__mcp-safari__*. The default, since a Chrome admin policy can
#           block the Claude in Chrome extension.
#   chrome  Claude in Chrome: `claude --chrome` connects to the extension in OWNER's own Chrome; its
#           tools are mcp__claude-in-chrome__*.
SAFARI_SERVER = "mcp__mcp-safari"
CHROME_SERVER = "mcp__claude-in-chrome"
BROWSER_SERVERS = {"safari": SAFARI_SERVER, "chrome": CHROME_SERVER}
# A report that starts with this marks the errand failed: the run found no browser and stopped.
BROWSER_MISSING = "BROWSER MISSING:"

_BUILT_IN = ("The Claude desktop app's built-in browser (Claude_Browser) cannot reach a run the game starts, so "
             "where a skill or note says to use the built-in browser (or Claude in Chrome), use {name} instead; "
             "sign-ins are {app}'s, not the app's. ")
_STOP = ("stop at once: begin the report with \"" + BROWSER_MISSING + "\" and say what was missing ({what}). "
         "Do not try a workaround, such as reading browser data off the disk.")

BROWSER_RULES = {
    "safari": ("The browser in this run is Safari through MCPSafari (the mcp__mcp-safari__* tools), in OWNER's own "
               "Safari. " + _BUILT_IN.format(name="the mcp-safari tools", app="Safari")
               + "If a status call reports the bridge as disconnected (or a port other than the one asked for), "
               "wait a few seconds and try once more; it often connects on the second call. If the "
               "mcp__mcp-safari__ tools are missing, or they still cannot reach Safari after that one retry, "
               + _STOP.format(what="Safari closed, the extension not connected, a site not signed in") + "\n\n"
               "Safari asks OWNER before the extension touches a site it has not been granted (Website access: "
               "Ask), and nobody is there to answer, so a step on an ungranted site may wait or time out. Treat "
               "that as a blocker: stop that step, name the site in the report so OWNER can grant it, and never "
               "work around the prompt.\n\n"
               "Krooster is a fallback source only, and OWNER is not logged in there in Safari. If a skill step "
               "needs Krooster, skip that step and say in the report that you skipped it and why."),
    "chrome": ("The browser in this run is Claude in Chrome (the mcp__claude-in-chrome__* tools), in OWNER's own "
               "Chrome. " + _BUILT_IN.format(name="Claude in Chrome", app="Chrome")
               + "If the mcp__claude-in-chrome__ tools are missing, or their first call cannot reach Chrome, "
               + _STOP.format(what="Chrome closed, the extension not connected, a site not signed in")),
    "off": ("This run has no browser. If the errand needs one, stop at once and begin the report with \""
            + BROWSER_MISSING + "\"."),
}

ERRAND_RULES = """# Errand

You are Tally, on an errand OWNER approved in The Obsidian Vault game. The brief below is the
whole task: do it, and nothing beyond it. You run unattended, so nobody can answer a question. Where
the brief is unclear, take the most likely reading and say which one you took in the report. If the
errand cannot be done safely or needs OWNER's decision, stop and say so in the report instead of guessing.

The session-start steps in bootstrap.md were done for you: skip them. Do not write to soul.md; the
Tally in the game logs what matters when it hears your report. Your core rules still hold: check your
work against the files before you call it done, say what you could not verify, and flag any workaround
at once in the report.

End with a short report in plain Markdown (no tables): what you did, what you checked and how, what is
left, and anything that needs OWNER's decision. Put any link or branch name OWNER needs in it.
Reports end up on the notice board and code ends up in a public repo, so name local folders by a
short form ("the Private vault", "~/…/<last folder>"), never by their full path, and write no email
addresses, phone numbers or other personal details where a short form will do.
"""

KIND_RULES = {
    "research": "Kind: research. You can read the Private vault (your working folder), the Work vault if it was "
                "added, and the web. You cannot write anything: the report is the result.",
    "code": "Kind: code. You are in your own git worktree of The Obsidian Vault on the branch {branch}; the copy "
            "of the game that is running is elsewhere and must not be touched. Make the change, follow the "
            "README's conventions, run `python3 -m unittest discover -s tests`, and commit with a clear "
            "message. Keep personal paths and data out of the repo: they belong in the local config "
            "(see config.py), and tests/test_pii.py fails if a tracked file holds one. Then push this branch only, with `git push -u origin {branch}`. Never push to main or "
            "merge anything. If the push fails, say so; the commits stay on the branch.",
    "session": "Kind: session. You have OWNER's own Claude Code setup in the Private vault folder: OWNER's skills, "
               "connectors and the browser, and you may edit notes as a normal session would, following "
               "bootstrap.md and the notes skill (load the skill named in the brief first, if there is one). "
               "{spend}",
}


def _now() -> float:
    return time.time()


class ErrandError(Exception):
    pass


class Errands:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.dir = cfg.state_dir / "errands"
        self.lock = threading.Lock()

    # ---------- records ----------

    def _path(self, eid: str) -> Path:
        return self.dir / f"{''.join(c for c in eid if c.isalnum())}.json"

    def get(self, eid: str) -> dict | None:
        try:
            return json.loads(self._path(eid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(rec["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path(rec["id"]))

    def all(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        recs = []
        for f in self.dir.glob("*.json"):
            try:
                recs.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(recs, key=lambda r: r.get("approved", 0), reverse=True)

    def running(self) -> list[dict]:
        return [r for r in self.all() if r.get("status") == "running"]

    def unreported(self) -> list[dict]:
        """Finished errands the talk session has not been told about yet. Marks them told."""
        with self.lock:
            out = []
            for r in self.all():
                if r.get("status") in ("done", "failed") and not r.get("reported"):
                    r["reported"] = _now()
                    self._save(r)
                    out.append(r)
            return out

    # ---------- starting ----------

    def start(self, op: dict) -> str:
        """Start an approved errand (an op from the ledger). Returns a line for OWNER."""
        if not self.cfg.errands:
            raise ErrandError("errands are switched off ([errands] enabled = false in config.toml)")
        if not self.cfg.claude_path:
            raise ErrandError("Claude Code was not found, so errands cannot run")
        if len(self.running()) >= self.cfg.errand_max_running:
            raise ErrandError(f"{self.cfg.errand_max_running} errands are already running; approve this one when one finishes")
        kind = op.get("errand_kind")
        if kind not in KINDS:
            raise ErrandError(f"unknown errand kind {kind!r}")
        if kind == "session":
            problem = self.browser_problem()
            if problem:  # fail before anything runs, so the errand does not sit there with no browser
                raise ErrandError(problem)
        rec = {"id": op["id"], "kind": kind, "title": op.get("title", ""), "brief": op.get("brief", ""),
               "spend_usd": float(op.get("spend_usd") or 0), "status": "running", "approved": _now(),
               "self_started": bool(op.get("self_started")),
               "log": str(self.dir / f"{op['id']}.log")}
        cwd = self._workdir(rec)
        cmd = self.command(rec)
        self._save(rec)
        threading.Thread(target=self._run, args=(rec, cmd, cwd), daemon=True).start()
        where = f" on branch {rec['branch']}" if rec.get("branch") else ""
        return f"Errand started{where}: {rec['title']}. Tally will hear the report when it is done."

    def _workdir(self, rec: dict) -> str:
        if rec["kind"] == "code":
            repo = self.cfg.errand_repo
            branch = f"errand/{rec['id']}"
            path = self.cfg.errand_worktrees / rec["id"]
            path.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch, str(path), "HEAD"],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise ErrandError(f"could not make a worktree for the errand: {(proc.stderr or proc.stdout).strip()[-300:]}")
            rec["branch"], rec["worktree"] = branch, str(path)
            rec["base"] = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip()
            return str(path)
        if not self.cfg.core_dir or not self.cfg.core_dir.is_dir():
            raise ErrandError("the vault folder is not set, so this errand has nowhere to run")
        return str(self.cfg.core_dir)

    # ---------- the browser ----------

    def browser(self) -> str:
        """The browser session errands get: "safari", "chrome" or "off"."""
        b = (self.cfg.errand_browser or "off").lower()
        return b if b in BROWSER_RULES else "off"

    def browser_app(self) -> str:
        """The process that must be running for the browser, or "" when there is none to check."""
        return self.cfg.errand_browser_app or BROWSER_APPS.get(self.browser(), "")

    def _app_running(self, app: str) -> bool:
        """Whether a process with exactly this name runs (pgrep; reads the process list, nothing else)."""
        return subprocess.run(["pgrep", "-x", app], capture_output=True).returncode == 0

    def browser_problem(self) -> str:
        """What OWNER has to start before a session errand can have the browser, or "" if nothing."""
        browser, app = self.browser(), self.browser_app()
        if browser == "off" or not app:
            return ""
        try:
            if self._app_running(app):
                return ""
        except OSError:  # no pgrep: cannot tell, so the run checks for itself (BROWSER_RULES)
            return ""
        how = {"safari": "the MCPSafari extension, with the extension turned on",
               "chrome": "the Claude in Chrome extension signed in to OWNER's Claude account"}[browser]
        tool = {"safari": "Safari through MCPSafari", "chrome": "Claude in Chrome"}[browser]
        return self.cfg.fill(f"{app} is not open, and session errands use the browser through {tool}. "
                             f"Open {app} with {how}, then approve the errand again.")

    def system_prompt(self, rec: dict) -> str:
        core, _, _ = load_core(self.cfg)
        if rec["spend_usd"] > 0:
            spend = (f"OWNER approved spending up to ${rec['spend_usd']:.2f} of OWNER's art credits on this errand. "
                     "Quote before each generation and stop before the total would pass that.")
        else:
            spend = ("Tools that spend OWNER's art credits are blocked. If the errand needs one, get a quote, "
                     "put it in the report and stop, so OWNER can approve the spend as a new errand.")
        rules = KIND_RULES[rec["kind"]].format(branch=rec.get("branch", ""), spend=spend)
        if rec["kind"] == "session":
            rules += "\n\n" + BROWSER_RULES[self.browser()]
        return (self.cfg.fill("# Shared core\n\nThese files are Tally's identity, shared by every use case.\n\n")
                + f"{core}\n\n" + self.cfg.fill(f"{ERRAND_RULES}\n{rules}\n"))

    def command(self, rec: dict) -> list[str]:
        cfg = self.cfg
        cmd = [cfg.claude_path or "claude", "-p", "--output-format", "json",
               "--append-system-prompt", self.system_prompt(rec)]
        kind = rec["kind"]
        if kind == "research":
            tools = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
            cmd += ["--tools", ",".join(tools), "--allowedTools", ",".join(tools),
                    "--permission-mode", "dontAsk", "--strict-mcp-config"]
            if cfg.work_dir and cfg.work_dir.is_dir():
                cmd += ["--add-dir", str(cfg.work_dir)]
        elif kind == "code":
            branch = rec["branch"]
            allowed = ["Read", "Glob", "Grep", "Edit", "Write",
                       "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
                       "Bash(git add:*)", "Bash(git commit:*)", f"Bash(git push -u origin {branch})",
                       "Bash(python3 -m unittest:*)", "Bash(python3 -m obsidian_vault.check_map:*)",
                       "Bash(ls:*)"]
            cmd += ["--tools", "Read,Glob,Grep,Edit,Write,Bash", "--allowedTools", ",".join(allowed),
                    "--disallowedTools", "Bash(git push --force:*),Bash(git push -f:*),Bash(git merge:*),Bash(git rebase:*)",
                    "--permission-mode", "acceptEdits", "--strict-mcp-config"]
        else:  # session: the owner's own setup
            browser = self.browser()
            server = BROWSER_SERVERS.get(browser)
            # One browser per run: the other one's tools are left out, and none at all when it is off.
            allowed = [t for t in cfg.errand_session_tools
                       if t == server or not any(t == s or t.startswith(s + "__")
                                                 for s in BROWSER_SERVERS.values())]
            if server and server not in allowed:  # no prompt can be answered, so the browser must be allowed
                allowed.append(server)
            if browser == "chrome":
                cmd += ["--chrome"]  # print mode does not connect to Claude in Chrome unless asked
            cmd += ["--allowedTools", ",".join(allowed), "--permission-mode", "acceptEdits"]
            if rec["spend_usd"] <= 0:
                cmd += ["--disallowedTools", ",".join(COST_TOOLS)]
            if cfg.work_dir and cfg.work_dir.is_dir():
                cmd += ["--add-dir", str(cfg.work_dir)]
        if cfg.errand_model:
            cmd += ["--model", cfg.errand_model]
        return cmd

    # ---------- running ----------

    def _run(self, rec: dict, cmd: list[str], cwd: str) -> None:
        prompt = f"Errand: {rec['title']}\n\n{rec['brief']}"
        timeout = max(60.0, self.cfg.errand_timeout_minutes * 60)
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  cwd=cwd, timeout=timeout)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            out = e.stdout if isinstance(e.stdout, str) else ""
            err, code = f"timed out after {timeout / 60:g} minutes", -1
        except OSError as e:
            out, err, code = "", str(e), -1
        try:
            Path(rec["log"]).write_text(f"$ exit {code}\n--- stdout\n{out}\n--- stderr\n{err}\n", encoding="utf-8")
        except OSError:
            pass
        result, failed = "", code != 0
        try:
            data = json.loads(out)
            result = str(data.get("result") or "")
            failed = failed or bool(data.get("is_error"))
            rec["cost_usd"] = data.get("total_cost_usd")
            rec["turns"] = data.get("num_turns")
        except (ValueError, AttributeError):
            result = (out or "").strip()[-4000:]
        if result.lstrip().startswith(BROWSER_MISSING):  # the run had no browser and stopped, as told
            failed = True
            rec["browser_missing"] = True
        if failed and not result:
            result = (err or "").strip()[-1500:] or f"claude exited with code {code}"
        if rec["kind"] == "code" and rec.get("worktree"):
            log = subprocess.run(["git", "-C", rec["worktree"], "log", "--oneline", f"{rec['base']}..HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            rec["commits"] = log.splitlines()
        rec.update(status="failed" if failed else "done", finished=_now(), result=result)
        with self.lock:
            self._save(rec)

    # ---------- for Tally ----------

    def summary(self, rec: dict, full: bool = False) -> str:
        head = f"Errand {rec['id']} ({rec['kind']}): {rec.get('title', '')}. Status: {rec.get('status')}."
        if rec.get("branch"):
            head += f" Branch: {rec['branch']}."
        if rec.get("commits") is not None:
            head += f" Commits: {len(rec['commits'])}."
        if rec.get("status") in ("done", "failed"):
            text = rec.get("result") or "(no report)"
            if not full and len(text) > 1500:
                text = text[:1500] + " ... (the full report is on the notice board, or call errand_report)"
            head += f"\nReport:\n{text}"
        return head
