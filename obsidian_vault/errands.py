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

On approval OWNER picks "run now" (it starts at once, as before) or "when there's room": then the
errand is recorded as "scheduled" and the scheduler (scheduler.py, plain code on a timer) starts it
when the usage meter says its projected cost fits. Every finished run records its tokens and an
estimated cost at API prices; the projection for a kind is the average of its recent runs.

Records live in <state_dir>/errands/<id>.json; the full output in <id>.log.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .config import BROWSER_APPS, Config, short_path
from .persona import load_core
from . import dashboards, usage

KINDS = ("research", "code", "session")
# What an errand of each kind is expected to cost before any has been measured, in dollars at API
# prices. Code errands ran $2.30 to $2.90 in the 2026-11-26 usage report; the others are starting
# guesses that the running average replaces as soon as one run of that kind is recorded.
DEFAULT_PROJECTION = {"research": 1.00, "code": 2.60, "session": 2.60}
AVERAGE_OF = 10  # the projection is the average of this many most recent measured runs of a kind
INTERRUPTED = ("Interrupted: the game was restarted while this errand ran, so it never finished. "
               "Nothing came back from it; queue it again to rerun it.")
REQUEUED = ("Interrupted: the game was restarted while this errand ran. It was scheduled, so it is back in "
            "the queue and will start again when there is room.")
# Every turn of a run re-reads the whole context, so what a run reads early is paid for on every turn
# after it. These keep a session errand small.
LEAN_RULES = ("Keep the run lean: every file and page you read stays in context and is paid for again on each "
              "later turn. Do not Read a big data file (such as a JSON history) whole: load and change it with a "
              "short python3 script through Bash and print only the part you need (the latest entry, one key). "
              "Read a long note by its headings first and then only the section you need. Prefer reading page "
              "text or one element over whole-page dumps or screenshots.")
DASHBOARD_RULES = ("The game itself builds and publishes these dashboards from their data files once you are done, "
                   "with no model, so keep only the data file up to date. Do not write, regenerate or publish the "
                   "page yourself, and do not say in the report that it could not be published: the game adds a "
                   "line about it.\n{list}")

RESUMED = ("This errand was cut off by a restart once and has been started again. For a code errand the branch "
           "may already hold commits from the first try: look at them first and keep what is sound.")

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
               + "The bridge often reports disconnected on the very first call while the extension reconnects: "
               "wait about 5 seconds and call again, up to 3 tries in all. A port other than the usual one (for "
               "example 8090 instead of 8089) only means another program held the usual port when this run "
               "started; it is not a failure, so carry on as soon as a status call says connected or Safari "
               "answers, and do not mention it in the report unless Safari never answered. If the "
               "mcp__mcp-safari__ tools are missing, or they still cannot reach Safari after 3 tries, "
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
    def __init__(self, cfg: Config, on_finish=None):
        self.cfg = cfg
        self.dir = cfg.state_dir / "errands"
        self.lock = threading.Lock()
        self.on_finish = on_finish  # called after a run ends (the scheduler looks for room then)

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

    def close_interrupted(self) -> list[str]:
        """At server start: an errand runs as a thread of the server, so one still marked "running" was cut
        off by a restart and will never finish. Either way Tally hears that it was interrupted, and it no
        longer counts against max_running, which would otherwise block every approval. One OWNER approved
        "when there's room" goes back to the queue; one started "now" is marked failed. Returns the titles."""
        closed = []
        for rec in self.running():
            if rec.get("scheduled"):
                rec.update(status="scheduled", interrupted=int(rec.get("interrupted") or 0) + 1,
                           interrupt_reported=False, result=REQUEUED)
            else:
                rec.update(status="failed", finished=_now(), result=INTERRUPTED)
            self._save(rec)
            closed.append(rec.get("title", rec["id"]))
        return closed

    def running(self) -> list[dict]:
        return [r for r in self.all() if r.get("status") == "running"]

    def scheduled(self) -> list[dict]:
        """Errands OWNER approved "when there's room", in the order they will start: by OWNER's priority,
        otherwise oldest first."""
        recs = [r for r in self.all() if r.get("status") == "scheduled"]
        return sorted(recs, key=lambda r: (float(r.get("order", r.get("approved", 0))), float(r.get("approved", 0))))

    def unreported(self) -> list[dict]:
        """Finished errands the talk session has not been told about yet, and scheduled ones a restart
        cut off and put back in the queue. Marks them told."""
        with self.lock:
            out = []
            for r in self.all():
                if r.get("status") in ("done", "failed") and not r.get("reported"):
                    r["reported"] = _now()
                    self._save(r)
                    out.append(r)
                elif r.get("status") == "scheduled" and r.get("interrupted") and not r.get("interrupt_reported"):
                    r["interrupt_reported"] = _now()
                    self._save(r)
                    out.append(r)
            return out

    # ---------- what errands cost ----------

    def projection(self, kind: str) -> tuple[float, int]:
        """(expected dollars, how many measured runs that rests on) for an errand of this kind: the
        average of its most recent measured runs, or the starting guess when none was measured yet."""
        runs = [r for r in self.all() if r.get("kind") == kind and isinstance(r.get("est_usd"), (int, float))
                and r.get("status") in ("done", "failed") and not r.get("self_started")]
        runs.sort(key=lambda r: r.get("finished", 0), reverse=True)
        runs = runs[:AVERAGE_OF]
        if not runs:
            return DEFAULT_PROJECTION.get(kind, max(DEFAULT_PROJECTION.values())), 0
        return sum(float(r["est_usd"]) for r in runs) / len(runs), len(runs)

    def _record_cost(self, rec: dict, data: dict | None) -> None:
        """Tokens and an estimated cost at API prices for a finished run: from the run's own JSON (its
        per-model totals), else from Claude Code's logs for its session."""
        found = usage.tokens_from_result(data) if isinstance(data, dict) else None
        tokens = found[0] if found else None
        if tokens is None and isinstance(data, dict) and data.get("session_id"):
            tokens = usage.session_tokens(Path(self.cfg.usage_logs_dir or usage.default_logs_dir()),
                                          str(data["session_id"]), since=float(rec.get("started") or 0) - 60)
        if tokens is None:
            return
        rec["tokens"] = {k: int(tokens.get(k, 0)) for k in ("input", "output", "cache_read", "cache_write")}
        rec["est_usd"] = round(float(tokens.get("usd", 0)), 2)

    # ---------- starting ----------

    def _check(self, op: dict) -> str:
        if not self.cfg.errands:
            raise ErrandError("errands are switched off ([errands] enabled = false in config.toml)")
        if not self.cfg.claude_path:
            raise ErrandError("Claude Code was not found, so errands cannot run")
        kind = op.get("errand_kind") or op.get("kind")
        if kind not in KINDS:
            raise ErrandError(f"unknown errand kind {kind!r}")
        return kind

    def _new_record(self, op: dict, kind: str, status: str) -> dict:
        return {"id": op["id"], "kind": kind, "title": op.get("title", ""), "brief": op.get("brief", ""),
                "spend_usd": float(op.get("spend_usd") or 0), "status": status, "approved": _now(),
                "self_started": bool(op.get("self_started")),
                "log": str(self.dir / f"{op['id']}.log")}

    def start(self, op: dict) -> str:
        """Start an approved errand (an op from the ledger) now. Returns a line for OWNER."""
        kind = self._check(op)
        if len(self.running()) >= self.cfg.errand_max_running:
            raise ErrandError(f"{self.cfg.errand_max_running} errands are already running; approve this one when one finishes")
        if kind == "session":
            problem = self.browser_problem()
            if problem:  # fail before anything runs, so the errand does not sit there with no browser
                raise ErrandError(problem)
        rec = self._new_record(op, kind, "running")
        self._launch(rec)
        where = f" on branch {rec['branch']}" if rec.get("branch") else ""
        return f"Errand started{where}: {rec['title']}. Tally will hear the report when it is done."

    def schedule(self, op: dict) -> str:
        """Put an approved errand in the queue, to start when there is room (scheduler.py). Returns a line for OWNER."""
        kind = self._check(op)
        if self.get(op["id"]):
            raise ErrandError("that errand is already recorded")
        rec = self._new_record(op, kind, "scheduled")
        rec.update(scheduled=True, order=rec["approved"])
        with self.lock:
            self._save(rec)
        est, n = self.projection(kind)
        basis = f"the average of {n} {kind} errand{'s' if n != 1 else ''}" if n else "a starting guess"
        return (f"Errand scheduled: {rec['title']}. It starts on its own when there is room for about ${est:.2f} "
                f"({basis}). Tally will hear the report when it is done.")

    def start_scheduled(self, eid: str) -> dict:
        """Start one scheduled errand now (the scheduler calls this once it has found room). Raises
        ErrandError if it may not start; a browser that is not open leaves it waiting in the queue."""
        with self.lock:
            rec = self.get(eid)
            if not rec or rec.get("status") != "scheduled":
                raise ErrandError("that errand is not waiting in the queue")
            self._check(rec)
            if self.running():
                raise ErrandError("another errand is running; scheduled errands go one at a time")
            if rec["kind"] == "session":
                problem = self.browser_problem()
                if problem:
                    raise ErrandError(problem)
            rec["status"] = "running"
            try:
                self._launch(rec)
            except ErrandError as e:  # e.g. no worktree could be made: it would fail the same way every time
                rec.update(status="failed", finished=_now(), result=f"The errand could not start: {e}")
                self._save(rec)
                raise
        return rec

    def move(self, eid: str, direction: str) -> list[dict]:
        """OWNER's priority: move a scheduled errand one place up or down the queue. Returns the queue."""
        if direction not in ("up", "down"):
            raise ErrandError("move it up or down")
        with self.lock:
            q = self.scheduled()
            i = next((n for n, r in enumerate(q) if r["id"] == eid), None)
            if i is None:
                raise ErrandError("that errand is not waiting in the queue")
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(q):
                for n, r in enumerate(q):  # make the order explicit, then swap the two
                    r["order"] = float(n)
                q[i]["order"], q[j]["order"] = float(j), float(i)
                for r in q:
                    self._save(r)
        return self.scheduled()

    def _launch(self, rec: dict) -> None:
        if rec.get("interrupted"):
            rec["result"] = ""
        rec["started"] = _now()
        cwd = self._workdir(rec)
        cmd = self.command(rec)
        self._save(rec)
        threading.Thread(target=self._run, args=(rec, cmd, cwd), daemon=True).start()

    def _workdir(self, rec: dict) -> str:
        if rec["kind"] == "code":
            repo = self.cfg.errand_repo
            branch = f"errand/{rec['id']}"
            path = self.cfg.errand_worktrees / rec["id"]
            if rec.get("worktree") and Path(rec["worktree"]).is_dir():  # restarted after a restart: keep its commits
                return rec["worktree"]
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
        core, _, _ = load_core(self.cfg, self.cfg.errand_core_files or None)
        if rec["spend_usd"] > 0:
            spend = (f"OWNER approved spending up to ${rec['spend_usd']:.2f} of OWNER's art credits on this errand. "
                     "Quote before each generation and stop before the total would pass that.")
        else:
            spend = ("Tools that spend OWNER's art credits are blocked. If the errand needs one, get a quote, "
                     "put it in the report and stop, so OWNER can approve the spend as a new errand.")
        rules = KIND_RULES[rec["kind"]].format(branch=rec.get("branch", ""), spend=spend)
        if rec["kind"] == "session":
            rules += "\n\n" + BROWSER_RULES[self.browser()] + "\n\n" + LEAN_RULES
        if rec["kind"] != "code":
            boards = dashboards.configured(self.cfg)
            if boards:
                listing = "\n".join(f"- {d.name}: data file {d.data_rel} in the vault folder"
                                     + (f", published to {d.artifact}" if d.artifact else "") for d in boards)
                rules += "\n\n" + DASHBOARD_RULES.format(list=listing)
        if rec.get("interrupted"):
            rules += "\n\n" + RESUMED
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
        model = self.model_for(kind)
        if model:
            cmd += ["--model", model]
        return cmd

    def model_for(self, kind: str) -> str:
        """The model an errand of this kind runs on: [errands] <kind>_model, else [errands] model, else
        Claude Code's own default ("")."""
        return self.cfg.errand_kind_models.get(kind) or self.cfg.errand_model

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
        result, failed, data = "", code != 0, None
        try:
            data = json.loads(out)
            result = str(data.get("result") or "")
            failed = failed or bool(data.get("is_error"))
            rec["cost_usd"] = data.get("total_cost_usd")
            rec["turns"] = data.get("num_turns")
        except (ValueError, AttributeError):
            data = None
            result = (out or "").strip()[-4000:]
        try:
            self._record_cost(rec, data)
        except Exception:  # noqa: BLE001 - a cost that cannot be counted never loses the report
            pass
        if result.lstrip().startswith(BROWSER_MISSING):  # the run had no browser and stopped, as told
            failed = True
            rec["browser_missing"] = True
        if failed and not result:
            result = (err or "").strip()[-1500:] or f"claude exited with code {code}"
        if rec["kind"] != "code" and not failed:
            section = self._dashboards(rec)
            if section:
                result = result.rstrip() + "\n\n" + section
        if rec["kind"] == "code" and rec.get("worktree"):
            log = subprocess.run(["git", "-C", rec["worktree"], "log", "--oneline", f"{rec['base']}..HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            rec["commits"] = log.splitlines()
        rec.update(status="failed" if failed else "done", finished=_now(), result=result)
        with self.lock:
            self._save(rec)
        if self.on_finish:
            try:
                self.on_finish()
            except Exception:  # noqa: BLE001 - the scheduler's own timer tries again
                pass

    def _dashboards(self, rec: dict) -> str:
        """Build (and publish) the dashboards whose data the run changed and add the publish runs' cost to
        the errand's. Returns the report section about them ("" when no data changed)."""
        try:
            section, runs = dashboards.after_errand(self.cfg, float(rec.get("started") or 0), short=short_path)
        except Exception as e:  # noqa: BLE001 - a dashboard problem never loses the report
            section, runs = f"**Dashboards:** the game could not build them ({type(e).__name__}: {e}).", []
        if section:
            rec["dashboards"] = section
        for data in runs:
            found = usage.tokens_from_result(data) if isinstance(data, dict) else None
            if not found:
                continue
            tokens = found[0]
            mine = rec.setdefault("tokens", {k: 0 for k in ("input", "output", "cache_read", "cache_write")})
            for k in ("input", "output", "cache_read", "cache_write"):
                mine[k] = int(mine.get(k, 0)) + int(tokens.get(k, 0))
            rec["est_usd"] = round(float(rec.get("est_usd") or 0) + float(tokens.get("usd", 0)), 2)
        return section

    # ---------- for Tally ----------

    @staticmethod
    def cost_line(rec: dict) -> str:
        """'about $2.61 (1.2M tokens), an estimate from API prices', or "" when it was not measured."""
        if not isinstance(rec.get("est_usd"), (int, float)):
            return ""
        n = usage.total_tokens(rec.get("tokens") or {})
        size = f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}K" if n >= 1000 else str(n)
        return f"about ${rec['est_usd']:.2f} ({size} tokens), {usage.ESTIMATE_NOTE}"

    def summary(self, rec: dict, full: bool = False) -> str:
        head = f"Errand {rec['id']} ({rec['kind']}): {rec.get('title', '')}. Status: {rec.get('status')}."
        if rec.get("branch"):
            head += f" Branch: {rec['branch']}."
        if rec.get("commits") is not None:
            head += f" Commits: {len(rec['commits'])}."
        if self.cost_line(rec):
            head += f" Cost: {self.cost_line(rec)}."
        if rec.get("status") == "scheduled" and rec.get("interrupted"):
            head += f"\n{rec.get('result') or REQUEUED}"
        if rec.get("status") in ("done", "failed"):
            text = rec.get("result") or "(no report)"
            if not full and len(text) > 1500:
                text = text[:1500] + " ... (the full report is on the notice board, or call errand_report)"
            head += f"\nReport:\n{text}"
        return head
