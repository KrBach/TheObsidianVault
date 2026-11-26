"""The Vellum text parser: a 90s text adventure for OWNER and the vault cat. Scripted, no model calls.

A line typed in the talk box that starts with ">" comes here and never goes to Tally, Brass or any
model, so it costs nothing. The parser reads verb + object (+ a second object for "give X to Y"),
picks Vellum's answer by its mood, whether it sleeps and how much it likes whoever is typing
(wary, friendly or fond), and lets the existing cat rules (cat.py) decide what actually happens:
a meal resets hunger and makes it follow you, a pet can end with it walking off, every act that
lands raises its liking the usual way.

All the words are in world/vellum-parser.json, written by Tally (vault note
games/obsidian-vault-vellum-parser.md). The file is read for every line, so an edit shows at once.

The gift (rare, fond only, never for a guest) brings the title of a random note in the vault's
archive/ folder: the file name only. The note itself is never opened.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .cat import FOND_OF, Cat, CatError, steps
from .config import REPO_ROOT

WORDS_FILE = REPO_ROOT / "world" / "vellum-parser.json"
PUNCT = ".,!;:\"()[]"


def tile_at(world: dict, x, y) -> str:
    """The tile name at a cell of the built map (with Tally's changes), or ""."""
    try:
        row = world["map"][int(y)]
        return str(world.get("legend", {}).get(row[int(x)], ""))
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def tier(affinity: float, words: dict) -> str:
    t = words.get("tiers", {})
    if affinity >= float(t.get("fond_from", 0.75)):
        return "fond"
    if affinity >= float(t.get("friendly_from", 0.3)):
        return "friendly"
    return "wary"


class VellumParser:
    def __init__(self, cat: Cat, archive_dir: Path | None = None, rng=random.random,
                 choose=random.choice, words_file: Path = WORDS_FILE, companion: str = "Tally"):
        self.cat = cat
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.rng, self.choose = rng, choose
        self.words_file = words_file
        self.companion = companion

    def words(self) -> dict:
        return json.loads(self.words_file.read_text(encoding="utf-8"))

    # ---------- reading the line ----------

    def parse(self, line: str, words: dict | None = None) -> dict:
        """verb (the canonical one, a refusal, or None), the objects it names, the words it does not
        know, and the raw words after the stop words."""
        w = words or self.words()
        text = str(line or "").strip().lstrip(">").strip().lower()
        drop = set(w.get("stop_words", [])) | set(w.get("particles", []))
        tokens = []
        for raw in text.split():
            tok = raw if raw == "?" else raw.strip(PUNCT + "?")
            if tok and tok not in drop:
                tokens.append(tok)
        if not tokens:
            return {"verb": None, "word": "", "objects": [], "unknown": [], "tokens": []}
        verbs = {syn: verb for verb, syns in w["verbs"].items() for syn in syns}
        objects = {syn: obj for obj, syns in w["objects"].items() for syn in syns}
        first, rest = tokens[0], tokens[1:]
        verb = verbs.get(first)
        if verb is None and first in w.get("refusals", {}) and not first.startswith("_"):
            verb = "refuse:" + first
        found, unknown = [], []
        for tok in rest:
            if tok in objects:
                found.append(objects[tok])
            else:
                unknown.append(tok)
        return {"verb": verb, "word": first, "objects": found or ["vellum"], "unknown": unknown, "tokens": tokens}

    # ---------- answering ----------

    def respond(self, line: str, who: str, at, cat=None, waiting: int = 0, tile: str = "") -> dict:
        """One line from OWNER (who="owner") or a guest. at: where they stand. cat: where the browser
        has the cat now. waiting: items waiting in the ledger. tile: the tile under the cat.
        Returns {"ok", "text", "effect", ...} for the browser, like Cat.tend, plus "verb"."""
        w = self.words()
        guest = who != "owner"
        state = self.cat.get()
        if isinstance(cat, dict):
            state = {**state, **{k: cat[k] for k in ("x", "y", "mood", "asleep", "on") if k in cat}}
        dist = steps(at, state)
        p = self.parse(line, w)
        out = {"ok": True, "verb": p["verb"], "effect": "none", "model": False}

        def say(text, **extra):
            return {**out, "text": text, **extra}

        if dist is None or dist > int(w.get("range", 2)):
            return say(w["out_of_range"], ok=False)
        verb = p["verb"]
        place = w.get("brass_placeholders", {})
        if verb is None and not p["word"]:
            return say(place.get("empty") or w["help"])
        if p["word"] == "xyzzy":
            return say(w["refusals"]["xyzzy"]["text"])
        if verb is None:
            return say(w["unknown_verb"].replace("<word>", p["word"]), ok=False)
        if verb.startswith("refuse:"):
            r = w["refusals"][verb.split(":", 1)[1]]
            if p["unknown"]:
                return say(w["unknown_object"].replace("<word>", p["unknown"][0]), ok=False)
            allowed = r.get("objects")
            if allowed and not set(p["objects"]) & set(allowed):  # "eat bowl": not a word it refuses, just unknown
                return say(w["unknown_verb"].replace("<word>", p["word"]), ok=False)
            return say(r["text"])
        if verb != "talk" and p["unknown"]:
            return say(w["unknown_object"].replace("<word>", p["unknown"][0]), ok=False)
        if guest and verb not in w.get("guest_verbs", []):
            return say(place.get("guest", w["help"]), ok=False)

        level = tier(self.cat.affinity(who), w) if who in FOND_OF and not guest else "wary"
        fond = level == "fond"
        asleep = bool(state.get("asleep"))
        mood = state.get("mood") if state.get("mood") in ("sleepy", "curious", "hungry", "playful") else "sleepy"
        if state.get("hungry"):  # the server's clock decides hunger (cat.py), whatever the browser last saw
            mood = "hungry"
        if asleep:
            mood = "sleepy"
        objs = p["objects"]
        reach = int(w.get("pet_reach", 1))
        out["tier"] = level

        def tend(action):
            try:
                return self.cat.tend(who, action, at, cat)
            except CatError:
                return None

        def gift(res):
            if guest or not fond or self.rng() >= float(w.get("gift_odds", 0.05)):
                return res
            title = self._archive_title()
            if title:
                res = {**res, "text": res["text"] + " " + w["hidden"]["gift"].replace("<title>", title), "gift": title}
            return res

        def landed(res, text, **extra):
            keep = {k: res[k] for k in ("effect", "follow_seconds", "affinity", "stay_odds", "fondness",
                                        "fondness_text") if k in res}
            return {**out, **keep, "text": text, **extra}

        if verb == "look":
            if "tail" in objs:
                return say(w["look"]["tail"])
            if "paw" in objs:
                return say(w["look"]["paw"])
            if asleep and tile == "compass_rose":
                return say(w["hidden"]["compass"])
            return say(w["look"][mood])

        if verb == "smell":
            return say(w["smell"])
        if verb == "listen":
            return say(w["listen"]["sleepy" if mood == "sleepy" else "awake"])
        if verb == "wait":
            return say(w["wait"])
        if verb == "help":
            return say(w["help"])
        if verb == "talk":
            toks = set(p["tokens"][1:])
            if toks & {"tally", self.companion.lower()}:
                return say(w["talk"]["tally"])
            if toks & {"secret", "secrets"}:
                return say(w["talk"]["secret"])
            return say(w["talk"]["default"])

        if verb == "call":
            if mood == "sleepy":
                return say(w["call"]["sleepy"])
            res = tend("call")
            if not res or not res.get("ok"):
                return say(w["out_of_range"], ok=False)
            return landed(res, w["call"]["awake_fond" if fond else "awake"])

        if dist > reach:  # the rest needs a hand on the cat
            return say(place.get("out_of_reach", w["out_of_range"]), ok=False)

        if verb == "take":
            return say(w["take"]["fond" if fond else "not_fond"])

        if verb == "pet":
            res = tend("pet")
            if not res or not res.get("ok"):
                return say(place.get("out_of_reach", w["out_of_range"]), ok=False)
            where = str(state.get("on") or "")  # cat.json keeps 40 characters of it: "on the desk, sitting squarely..."
            on_ledger = "ledger" in where or where.startswith("on the desk")
            if on_ledger and waiting > 0 and not guest:
                return gift(landed({**res, "effect": "purr"}, w["hidden"]["ledger"]))
            if res["effect"] == "walk_off":
                return landed(res, w["pet"]["walk_off"])
            if "belly" in objs:
                text = w["pet"]["belly_fond" if fond else "belly"]
            elif "ears" in objs:
                text = w["pet"]["ears"]
            elif mood == "sleepy":
                text = w["pet"]["sleepy_fond" if fond else "sleepy"]
            else:
                text = w["pet"][mood]
            return gift(landed(res, text))

        if verb == "feed":
            if "quill" in objs:
                return say(w["feed"]["quill"])
            if mood != "hungry":
                return say(w["feed"]["other"])
            res = tend("feed")
            if not res or not res.get("ok"):
                return say(place.get("out_of_reach", w["out_of_range"]), ok=False)
            return gift(landed(res, w["feed"]["hungry"]))

        if verb == "play":
            if mood == "playful":
                res = tend("play")
                if res and res.get("ok") and res.get("effect") == "pounce":
                    return landed(res, w["play"]["playful"])
                return say(w["play"]["ignore"])
            if mood == "curious":
                return say(w["play"]["curious"])
            return say(w["play"]["ignore"])

        return say(w["unknown_verb"].replace("<word>", p["word"]), ok=False)

    def _archive_title(self) -> str:
        """The title of a random note in archive/: its file name. The note is never opened."""
        if not self.archive_dir or not self.archive_dir.is_dir():
            return ""
        notes = sorted(n for n in self.archive_dir.glob("*.md") if not n.name.startswith("."))
        return self.choose(notes).stem if notes else ""
