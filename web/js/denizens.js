// The vault's other residents, scripted in the browser: no model calls, so they cost nothing.
//
// Vellum, the cat, as in-game Tally designed it. It always has a mood: sleepy about half the time,
// otherwise curious or playful, and hungry from three hours after its last meal. The mood picks where
// it goes. Sleepy: warm places (the fireplace, the braziers), its cushion, the hammock, the couch.
// Curious: the pool's edge, whoever came into a room last, or the map table when someone works there.
// Playful: the loose pages and the quill on a string. Hungry: its bowl, and after a while Tally.
// When four or more items wait in the ledger it goes and sits on the desk. It never settles in or by
// a doorway, by the bell or on the welcome mat, and it never blocks anyone: people walk through it.
//
// Tending it (pet, feed, play, call) goes through the server, which has the rules (cat.py), the same
// for OWNER, a guest and Tally; this file plays out what the server says happened. main.js saves
// where it is and its mood, so the next session finds it there.
//
// The imp is the errand runner. When an errand starts it leaves the desk and runs out of the
// vault; when the errand is done it comes back in with the report and pins it to the notice board.
//
// Brass, the Artificer, has a temporary body that follows the life of a code errand. The server
// says which stage he is at (brass.py); this file walks him there. Queued: in by the front door to
// stand beside Tally's desk. Rejected: back out of the front door. Approved: to the Outbox table,
// tinkering while it runs. Done or failed: to the notice board, a pause, then out of the front door.
// He never stands in or by a doorway, by the bell, on the welcome mat or on the cat's cushion, and
// like the cat he blocks nobody.

const STEPS4 = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const STEPS8 = [...STEPS4, [1, 1], [1, -1], [-1, 1], [-1, -1]];
const WARM = new Set(["brazier", "fireplace", "fireplace_side"]);
const SOFT = { cushion: 6, hammock: 4, couch: 2 }; // where it likes to sleep, and how much
const LIFT = { desk: 17, map_table: 13, hammock: 10, couch: 6, cushion: 3 }; // how high it sits on each
const CAT_STEP_MS = 520, IMP_STEP_MS = 190, BRASS_STEP_MS = 430, BOARD_PAUSE_MS = 5000;
const HUNGRY_MS = 3 * 3600 * 1000;
const S = 1000;

const CAT_LOOKS = [
  "It looks back at you, slowly closes its eyes, and opens them again. You have been approved of.",
  "It is not interested in you right now. It is interested in a speck of light on the floor.",
  "It flicks one ear at you, which is as much as you are getting.",
  "It stares at you without blinking. You blink first.",
];

export function createDenizens(world, api) {
  const { tileAt, log, now = () => performance.now(), clock = () => Date.now(),
          people = () => ({}), roomAt = () => null, brassSeen = () => {} } = api;
  const def = world.creatures?.cat || {};
  const NAME = def.call || "the cat";
  const cell = tileAt;
  const id = (x, y) => tileAt(x, y)?.id;
  const open = (x, y) => { const t = tileAt(x, y); return !!(t && t.def && !t.def.solid); };
  const by = (x, y, ids, steps = STEPS8) => steps.some(([dx, dy]) => ids.has(id(x + dx, y + dy)));
  const isDoor = (x, y) => id(x, y) === "door";
  const nearDoor = (x, y) => isDoor(x, y) || STEPS4.some(([dx, dy]) => isDoor(x + dx, y + dy));
  const fixedSpots = () => [world.spawn, world.exit, world.tally].filter(Boolean).map(([x, y]) => `${x},${y}`);
  const BELL = new Set(["bell"]);
  // never settle here: a doorway, the bell, the welcome mat, the way in, Tally's place at the desk
  const forbidden = (x, y) => nearDoor(x, y) || id(x, y) === "mat" || by(x, y, BELL) || fixedSpots().includes(`${x},${y}`);
  const canSettle = (x, y) => open(x, y) && !forbidden(x, y);
  const dist = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
  const cellsWhere = (pred) => {
    const out = [], m = world.map; // read each time: Tally can rearrange the vault
    for (let y = 0; y < m.length; y++) for (let x = 0; x < m[y].length; x++) if (pred(x, y)) out.push([x, y]);
    return out;
  };
  const around = (x, y, ok = canSettle) => STEPS8.map(([dx, dy]) => [x + dx, y + dy]).filter(([a, b]) => ok(a, b));

  function bfs(start, goals, passable) {
    const key = (x, y) => `${x},${y}`;
    const goalSet = new Set(goals.map(([x, y]) => key(x, y)));
    if (goalSet.has(key(...start))) return [];
    const prev = new Map([[key(...start), null]]);
    const queue = [start];
    while (queue.length) {
      const [x, y] = queue.shift();
      for (const [dx, dy] of STEPS4) {
        const nx = x + dx, ny = y + dy, k = key(nx, ny);
        if (prev.has(k) || !passable(nx, ny)) continue;
        prev.set(k, [x, y]);
        if (goalSet.has(k)) {
          const path = [];
          for (let c = [nx, ny]; prev.get(key(...c)); c = prev.get(key(...c))) path.unshift(c);
          return path;
        }
        queue.push([nx, ny]);
      }
    }
    return null;
  }

  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const weighted = (list) => {
    const total = list.reduce((s, p) => s + p.w, 0);
    let r = Math.random() * total;
    for (const p of list) { r -= p.w; if (r <= 0) return p; }
    return list[list.length - 1] || null;
  };
  const span = (a, b) => (a + Math.random() * (b - a)) * S;

  const start0 = pick(cellsWhere((x, y) => canSettle(x, y) && by(x, y, WARM))) || world.spawn;
  const cat = { kind: "cat", x: start0[0], y: start0[1], rx: start0[0], ry: start0[1], path: [], pace: CAT_STEP_MS,
                nextStep: 0, restUntil: now() + 15 * S, mood: "sleepy", sleeping: false, batting: false,
                perch: null, lift: 0, onDesk: false, goal: null, follow: null, come: null, hunger: null,
                purrUntil: 0, pounceUntil: 0, fedAt: clock(),
                // how much it likes each of them (0 to 1) and its odds of staying when they come over:
                // both from the server (cat.py), which keeps them. They shift its odds, never decide them.
                affinity: { player: 0, tally: 0 }, stayOdds: { player: 0, tally: 0 }, fondness: {}, fondnessText: "" };
  const guest = world.role && world.role !== "owner"; // a guest is not the one it knows
  const imp = { kind: "imp", x: 0, y: 0, rx: 0, ry: 0, path: [], hidden: true, alpha: 0, job: null, nextStep: 0, carrying: false };
  const out = [];  // errands the imp is out on
  const back = []; // reports waiting to be brought in

  // Who came into a room last: the curious cat goes to have a look at them.
  // And who is walking towards it: someone it likes coming over makes it less likely to wander off.
  const lastRoom = {}, lastDist = {}, nearingUntil = {};
  let lastArrived = "player";
  function watchArrivals() {
    const ppl = people(), t = now();
    for (const who of ["player", "tally"]) {
      const p = ppl[who];
      if (!p) continue;
      const r = roomAt(p.x, p.y)?.id || "";
      if (lastRoom[who] !== undefined && lastRoom[who] !== r) lastArrived = who;
      lastRoom[who] = r;
      const d = dist([cat.x, cat.y], [p.x, p.y]);
      if (lastDist[who] !== undefined && d < lastDist[who] && d <= 8) nearingUntil[who] = t + 4 * S;
      lastDist[who] = d;
    }
  }
  const nearing = (who) => now() < (nearingUntil[who] || 0);

  // Affinity from the server, for whoever it is about (owner is the player here).
  function setAffinity(src) {
    if (!src?.affinity) return;
    const a = src.affinity, s = src.stay_odds || {};
    cat.affinity = { player: guest ? 0 : +a.owner || 0, tally: +a.tally || 0 };
    cat.stayOdds = { player: guest ? 0 : +s.owner || 0, tally: +s.tally || 0 };
    cat.fondness = src.fondness || {};
    cat.fondnessText = src.fondness_text || "";
  }
  // The one it likes most of those in the vault, if it likes them at all above a passing acquaintance.
  function favourite() {
    const ppl = people();
    const here = ["player", "tally"].filter((w) => ppl[w]);
    if (!here.length) return null;
    const who = here.reduce((a, b) => (cat.affinity[b] > cat.affinity[a] ? b : a));
    return cat.affinity[who] > 0.2 ? who : null;
  }

  // ---------- the cat ----------

  const hungry = () => clock() - cat.fedAt >= HUNGRY_MS;

  function pickMood() {
    const r = Math.random();
    return r < 0.5 ? "sleepy" : r < 0.78 ? "curious" : "playful";
  }

  // Where it might go in a mood: [{at, w, sleep?, bat?, perch?}]
  function places(mood) {
    const list = [], ppl = people();
    const spread = (cells, w, extra) => { for (const at of cells) list.push({ at, w: w / cells.length, ...extra }); };
    const fav = favourite(), fond = fav ? cat.affinity[fav] : 0;
    if (mood === "sleepy") {
      spread(cellsWhere((x, y) => canSettle(x, y) && by(x, y, WARM)), 5, { sleep: true });
      for (const [kind, w] of Object.entries(SOFT)) spread(cellsWhere((x, y) => id(x, y) === kind && canSettle(x, y)), w, { sleep: true });
      // near whoever it likes most (a weight among the others, not a rule) ...
      if (fav) spread(around(ppl[fav].x, ppl[fav].y), 5 * fond, { sleep: true });
      // ... and, once it is really fond of Tally, now and then curled up by Tally's place at the desk
      if (cat.affinity.tally >= 0.6 && world.tally) spread(around(world.tally[0], world.tally[1]), 3 * cat.affinity.tally, { sleep: true });
    } else if (mood === "curious") {
      if (fav) spread(around(ppl[fav].x, ppl[fav].y), 5 * fond, { watch: fav, fav: true });
      spread(cellsWhere((x, y) => canSettle(x, y) && by(x, y, new Set(["pool"]), STEPS4)), 3, {});
      const who = ppl[lastArrived];
      if (who) spread(around(who.x, who.y), 4, { watch: lastArrived });
      // the map table, but only when someone is working at it
      const tables = [];
      for (const w of ["player", "tally"]) {
        const p = ppl[w];
        if (p) for (const at of around(p.x, p.y, (x, y) => id(x, y) === "map_table")) tables.push(at);
      }
      spread(tables, 5, { perch: true });
    } else if (mood === "playful") {
      spread(cellsWhere((x, y) => id(x, y) === "papers" && canSettle(x, y)), 3, { bat: true });
      spread(cellsWhere((x, y) => id(x, y) === "quill_toy" && canSettle(x, y)), 3, { bat: true });
    }
    return list.filter((p) => p.perch || p.at[0] !== cat.x || p.at[1] !== cat.y);
  }

  function go(target, t, pace = CAT_STEP_MS) {
    const goals = target.perch ? around(target.at[0], target.at[1], open) : [target.at];
    const path = bfs([cat.x, cat.y], goals, open);
    if (!path) { cat.restUntil = t + 20 * S; return false; }
    cat.path = path; cat.goal = target; cat.pace = pace;
    if (!path.length) arrive(t);
    return true;
  }

  function leavePerch() {
    if (cat.perch) {
      const off = around(cat.x, cat.y, open)[0];
      if (off) [cat.x, cat.y] = off;
    }
    cat.perch = null; cat.onDesk = false; cat.lift = 0;
  }

  function choose(t, busy) {
    if (busy && cat.mood !== "hungry") {
      // the ledger is busiest: go and sit on it
      const desks = cellsWhere((x, y) => id(x, y) === "desk");
      if (desks.length) { go({ at: desks[Math.floor(desks.length / 2)], perch: true, desk: true }, t); return; }
    }
    if (cat.mood === "hungry") {
      // the bowl first; after waiting a while there, Tally; then the bowl again
      const ppl = people();
      if (cat.hunger === "bowl" && ppl.tally) { cat.hunger = "tally"; go({ at: [ppl.tally.x, ppl.tally.y], perch: true, nearOnly: true, hungry: true }, t); return; }
      const bowl = cellsWhere((x, y) => id(x, y) === "cat_bowl")[0];
      cat.hunger = "bowl";
      if (bowl) { go({ at: bowl, perch: true, nearOnly: true, hungry: true }, t); return; }
    }
    let opts = places(cat.mood);
    if (!opts.length) opts = places("sleepy");
    const target = weighted(opts);
    if (!target) { cat.restUntil = t + 30 * S; return; }
    go(target, t);
  }

  function arrive(t) {
    const g = cat.goal || {};
    cat.goal = null;
    if (g.perch && !g.nearOnly) { // hop up onto the desk or the map table
      [cat.x, cat.y] = g.at;
      cat.perch = id(cat.x, cat.y);
      cat.onDesk = !!g.desk;
    } else if (forbidden(cat.x, cat.y) && !cat.follow) {
      // never settle in or by a doorway, the bell or the mat: walk on to the nearest good place
      const next = bfs([cat.x, cat.y], cellsWhere(canSettle), open);
      if (next?.length) { cat.path = next; cat.goal = { settleOnly: true }; return; }
    }
    cat.lift = LIFT[id(cat.x, cat.y)] || 0;
    cat.sleeping = !!g.sleep && cat.mood === "sleepy" && Math.random() < 0.85;
    cat.batting = !!g.bat;
    cat.restUntil = t + (g.desk ? 60 * S : g.hungry ? 120 * S : cat.sleeping ? span(90, 210)
      : cat.batting ? span(15, 35) : span(25, 60));
    // went to look at someone it is very fond of: sometimes it tags along for a few steps
    const fond = g.fav ? cat.affinity[g.watch] || 0 : 0;
    if (fond >= 0.6 && Math.random() < 0.3 * fond) cat.follow = { who: g.watch, until: clock() + span(12, 30) };
  }

  function catStep(t, busy) {
    watchArrivals();
    const ppl = people();
    if (hungry() && cat.mood !== "hungry" && !cat.follow) {
      cat.mood = "hungry"; cat.hunger = null;
      if (!cat.sleeping && !cat.path.length) cat.restUntil = Math.min(cat.restUntil, t + 5 * S);
    } else if (!hungry() && cat.mood === "hungry") {
      cat.mood = "playful"; cat.hunger = null;
    }
    if (cat.come && t >= cat.come.at) {
      // it heard you, and is pretending it didn't: up, then over, slowly
      const who = ppl[cat.come.who];
      cat.come = null;
      if (who) { leavePerch(); cat.sleeping = false; cat.batting = false; cat.mood = "curious";
        go({ at: [who.x, who.y], perch: true, nearOnly: true }, t, CAT_STEP_MS * 1.7); }
      return;
    }
    if (cat.path.length) {
      if (t < cat.nextStep) return;
      cat.nextStep = t + cat.pace * (0.8 + Math.random() * 0.5);
      [cat.x, cat.y] = cat.path.shift();
      cat.lift = 0;
      if (!cat.path.length) arrive(t);
      return;
    }
    if (cat.follow) {
      const who = ppl[cat.follow.who];
      if (clock() > cat.follow.until || !who) { cat.follow = null; cat.restUntil = t + span(20, 40); return; }
      if (dist([cat.x, cat.y], [who.x, who.y]) > 1) {
        const path = bfs([cat.x, cat.y], around(who.x, who.y, open), open);
        if (path?.length) { cat.path = path.slice(0, 3); cat.pace = CAT_STEP_MS * 0.7; cat.goal = { followStep: true }; }
      }
      return;
    }
    if (t < cat.restUntil) return;
    if (cat.perch === "desk" && busy) { cat.restUntil = t + 30 * S; return; } // still busy: stay on the ledger
    // someone it likes is coming over: it may wait for them (the odds are cat.py's, never certain)
    const coming = ["player", "tally"].filter(nearing);
    if (coming.length && cat.mood !== "hungry" && Math.random() < Math.max(...coming.map((w) => cat.stayOdds[w] || 0))) {
      cat.restUntil = t + span(10, 25); return;
    }
    const woke = cat.sleeping;
    leavePerch();
    cat.sleeping = false; cat.batting = false;
    if (cat.mood !== "hungry") cat.mood = woke && Math.random() < 0.5 ? "playful" : pickMood();
    choose(t, busy);
  }

  // What the server said happened when someone tended the cat (cat.py), played out here.
  function apply(res, who) {
    if (!res?.ok) return;
    setAffinity(res.cat?.affinity ? res.cat : res);
    const t = now();
    if (res.cat?.fed_at) cat.fedAt = res.cat.fed_at * 1000;
    switch (res.effect) {
      case "purr": cat.purrUntil = t + 6 * S; cat.restUntil = Math.max(cat.restUntil, t + 20 * S); break;
      case "blink": cat.purrUntil = 0; break;
      case "walk_off": {
        cat.sleeping = false; cat.batting = false; leavePerch(); cat.mood = "curious";
        const far = places("sleepy").concat(places("curious")).filter((p) => !p.perch && dist(p.at, [cat.x, cat.y]) > 4);
        const target = weighted(far);
        if (target) go({ ...target, sleep: false }, t);
        break;
      }
      case "fed":
        cat.fedAt = res.cat?.fed_at ? res.cat.fed_at * 1000 : clock();
        cat.mood = "playful"; cat.hunger = null; cat.sleeping = false; cat.batting = false; cat.path = [];
        leavePerch();
        cat.follow = { who, until: clock() + (res.follow_seconds || 60) * S };
        break;
      case "pounce": cat.pounceUntil = t + 2500; cat.batting = true; cat.restUntil = Math.max(cat.restUntil, t + 20 * S); break;
      case "come": cat.come = { who, at: t + span(1.5, 3.5) }; break;
      default: break;
    }
  }

  // Where it is, in words, for Tally's game_state and the look text.
  function whereWord() {
    if (cat.perch === "desk") return "on the desk, sitting squarely on the open ledger";
    if (cat.perch === "map_table") return "on the map table";
    const here = id(cat.x, cat.y);
    if (here === "cushion") return "on its cushion";
    if (here === "hammock") return "in the hammock";
    if (here === "couch") return "on the couch";
    if (here === "papers") return "among the loose pages";
    if (here === "quill_toy") return "by the quill on a string";
    if (by(cat.x, cat.y, new Set(["cat_bowl"]))) return "by its bowl";
    if (by(cat.x, cat.y, new Set(["fireplace", "fireplace_side"]))) return "by the fireplace";
    if (by(cat.x, cat.y, new Set(["brazier"]))) return "by a brazier";
    if (by(cat.x, cat.y, new Set(["pool"]), STEPS4)) return "at the pool's edge, watching the water";
    return "";
  }

  function doingWord() {
    if (cat.path.length || cat.come) return cat.follow ? "following someone about" : "on its way somewhere, with purpose";
    if (cat.sleeping) return "asleep, curled up nose to tail";
    if (cat.batting) return "batting at things with one paw";
    if (cat.mood === "hungry") {
      if (cat.hunger === "tally") return "hungry, and staring at Tally about it";
      return whereWord() === "by its bowl" ? "hungry, and waiting by its bowl" : "hungry";
    }
    return { sleepy: "drowsy", curious: "sitting and watching, curious", playful: "playful, tail twitching" }[cat.mood] || "sitting";
  }

  function snapshot() {
    return { x: cat.x, y: cat.y, mood: cat.mood, asleep: cat.sleeping, on: whereWord() };
  }

  function restore(saved) {
    if (saved?.fed_at) cat.fedAt = saved.fed_at * 1000;
    setAffinity(saved);
    if (!saved || saved.x == null || !canSettle(saved.x, saved.y)) return false;
    cat.x = cat.rx = saved.x; cat.y = cat.ry = saved.y;
    cat.mood = saved.mood || "sleepy";
    cat.sleeping = !!saved.asleep && cat.mood === "sleepy";
    cat.lift = LIFT[id(cat.x, cat.y)] || 0;
    cat.restUntil = now() + span(20, 60);
    return true;
  }

  // ---------- the imp ----------

  const exitIn = () => {
    // the floor cell just inside the way out
    const [ex, ey] = world.exit;
    return STEPS4.map(([dx, dy]) => [ex + dx, ey + dy]).find(([x, y]) => open(x, y)) || world.spawn;
  };
  const boardSide = () => {
    for (let y = 0; y < world.map.length; y++) {
      for (let x = 0; x < world.map[y].length; x++) {
        if (cell(x, y)?.id !== "notice_board") continue;
        const s = STEPS4.map(([dx, dy]) => [x + dx, y + dy]).find(([a, b]) => open(a, b));
        if (s) return s;
      }
    }
    return null;
  };
  const deskSide = () => {
    const [tx, ty] = world.tally;
    return [tx, ty + 1];
  };

  function impGo(from, to, job) {
    const path = bfs(from, [to], open);
    imp.x = imp.rx = from[0]; imp.y = imp.ry = from[1];
    imp.path = path || [];
    imp.job = job;
    imp.hidden = false;
    imp.alpha = 1;
  }

  function impStep(t) {
    if (imp.hidden) {
      if (back.length) {
        const e = back.shift();
        const side = boardSide();
        impGo(exitIn(), side || deskSide(), { kind: "pin", errand: e });
        imp.carrying = true;
        log(`The door opens and the imp scurries in with a rolled-up report: "${e.title}".`, "action");
      } else if (out.length) {
        const e = out.shift();
        impGo(deskSide(), exitIn(), { kind: "out", errand: e });
        imp.carrying = false;
        log(`An imp hops down from Tally's desk with the brief for "${e.title}" and runs for the door.`, "action");
      }
      return;
    }
    if (imp.path.length) {
      if (t < imp.nextStep) return;
      imp.nextStep = t + IMP_STEP_MS;
      [imp.x, imp.y] = imp.path.shift();
      return;
    }
    if (imp.job?.kind === "pin") {
      log("The imp pins the report to the notice board, bows to nobody in particular, and leaves.", "action");
      imp.carrying = false;
      imp.job = { kind: "leave" };
      imp.path = bfs([imp.x, imp.y], [exitIn()], open) || [];
      return;
    }
    // out of the door: fade away
    imp.alpha -= 0.08;
    if (imp.alpha <= 0) { imp.hidden = true; imp.job = null; }
  }

  // ---------- Brass, the Artificer ----------

  const brass = { kind: "brass", x: 0, y: 0, rx: 0, ry: 0, path: [], hidden: true, alpha: 0, nextStep: 0,
                  want: { present: false }, goal: null, arrived: false, pauseUntil: 0, tinker: false,
                  pinned: "", told: new Set(), wanderAt: null, restUntil: 0 };
  // where he may stand: not by a door, the bell, the mat or the way in (as for the cat), nor on its cushion
  const brassOk = (x, y) => open(x, y) && !forbidden(x, y) && id(x, y) !== "cushion";
  const stationOf = (goal) => (world.brass || {})[goal];
  // Between jobs he lives in the vault and potters about (scripted, no model calls): the Outbox
  // table, a brazier, the pool's edge, the map table, a reading chair. Earned autonomy can come later.
  const WARM_B = new Set(["brazier", "fireplace", "fireplace_side"]), BY_B = new Set(["pool", "map_table", "shelf", "archive_shelf", "scribe_desk", "bed"]);
  function wanderSpots() {
    const out = [];
    const ob = stationOf("outbox");
    if (ob) out.push(ob, ob, ob); // his bench, more often than not
    for (const c of cellsWhere((x, y) => brassOk(x, y) && (by(x, y, WARM_B) || by(x, y, BY_B, STEPS4)))) out.push(c);
    return out.filter(([x, y]) => brassOk(x, y) && !(x === cat.x && y === cat.y));
  }
  function newWander() {
    const spots = wanderSpots().filter(([x, y]) => dist([x, y], [brass.x, brass.y]) > 3);
    brass.wanderAt = spots.length ? pick(spots) : stationOf("outbox");
  }
  function standCell(goal) {
    if (goal === "wander") { if (!brass.wanderAt) newWander(); return brass.wanderAt; }
    if (goal === "door") return open(...world.exit) ? world.exit : exitIn();
    const a = stationOf(goal);
    if (!a) return null;
    if (brassOk(a[0], a[1])) return a;
    // the anchor is taken (Tally may have put the Outbox table right there): the nearest good cell beside it
    for (let r = 1; r <= 3; r++) {
      const ring = [];
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) === r && brassOk(a[0] + dx, a[1] + dy)) ring.push([a[0] + dx, a[1] + dy]);
      }
      ring.sort((p, q) => (Math.abs(p[0] - a[0]) + Math.abs(p[1] - a[1])) - (Math.abs(q[0] - a[0]) + Math.abs(q[1] - a[1])));
      if (ring.length) return ring[0];
    }
    return null;
  }
  const seenOnce = (eid) => { if (eid && !brass.told.has(eid)) { brass.told.add(eid); brassSeen(eid); } };
  // where he is going: the stage's place, or after pinning a report, wherever the server says is next
  function brassGoal() {
    const w = brass.want;
    if (!w?.present || w.stage === "leaving") return "wander"; // no job, or a rejected brief: back to his own time
    if (w.stage === "reporting" && brass.pinned === w.errand) return (w.then && w.then !== "door") ? w.then : "wander";
    return w.goes;
  }

  function brassArrive(goal, t) {
    const w = brass.want, title = w?.title || "an errand";
    brass.tinker = goal === "outbox";
    if (goal === "desk") log(`Brass sets his toolbox down beside Tally's desk and waits for you to look at "${title}" in the ledger.`, "action");
    else if (goal === "outbox") log(`Brass spreads his tools on the Outbox table and gets to work on "${title}".`, "action");
    else if (goal === "board") {
      log(`Brass pins a note to the notice board: "${title}" ${w.outcome === "failed" ? "failed" : "is done"}.`, "action");
      brass.pauseUntil = t + BOARD_PAUSE_MS;
    } else if (goal === "door") log("Brass tips his goggles to nobody in particular and goes out by the front door.", "action");
    else if (goal === "wander") {
      brass.tinker = dist([brass.x, brass.y], stationOf("outbox") || [-9, -9]) <= 1;
      brass.restUntil = t + (40 + Math.random() * 100) * 1000;
    }
  }

  function brassStep(t) {
    const w = brass.want;
    if (brass.hidden) {
      if (!w?.present) return;
      if (w.stage === "leaving") { seenOnce(w.errand); return; } // a rejection nobody saw him for: nothing to walk
      if (w.stage === "reporting" && brass.told.has(w.errand)) return;
      [brass.x, brass.y] = world.exit; brass.rx = brass.x; brass.ry = brass.y;
      brass.hidden = false; brass.alpha = 0; brass.goal = null; brass.arrived = false; brass.tinker = false;
      log(w.stage === "reporting"
        ? `The front door opens and Brass, the Artificer, stumps in with the report on "${w.title}".`
        : `The front door opens and Brass, the Artificer, stumps in with his toolbox, about "${w.title}".`, "action");
    }
    const goal = brassGoal();
    if (w?.present && w.stage === "leaving") seenOnce(w.errand); // a rejection: nothing to walk out for
    if (goal === "wander" && brass.goal === "wander" && brass.arrived && t >= brass.restUntil) { newWander(); brass.goal = null; }
    if (goal !== brass.goal) {
      if (goal === "wander" && w?.stage === "leaving" && brass.goal === "desk") log("Brass shrugs at the rejected brief and picks up his toolbox.", "action");
      if (goal === "wander" && brass.goal !== null && brass.goal !== "wander") newWander();
      brass.goal = goal; brass.arrived = false; brass.tinker = false;
      const to = standCell(goal);
      brass.path = (to && bfs([brass.x, brass.y], [to], open)) || [];
    }
    if (brass.path.length) {
      brass.alpha = Math.min(1, brass.alpha + 0.1);
      if (t < brass.nextStep) return;
      brass.nextStep = t + BRASS_STEP_MS;
      [brass.x, brass.y] = brass.path.shift();
      return;
    }
    if (!brass.arrived) { brass.arrived = true; brassArrive(goal, t); }
    if (goal === "door") {
      brass.alpha -= 0.06;
      if (brass.alpha <= 0) {
        brass.hidden = true; brass.goal = null; brass.tinker = false;
        if (w?.present && (w.stage === "leaving" || w.stage === "reporting")) seenOnce(w.errand);
      }
      return;
    }
    brass.alpha = Math.min(1, brass.alpha + 0.1);
    if (goal === "board" && w?.stage === "reporting" && brass.pinned !== w.errand && t >= brass.pauseUntil) {
      brass.pinned = w.errand; // walks on at once; the server hears it and says what is next
      seenOnce(w.errand);
    }
  }

  // The server's word on where he should be (brass.py), from the poll.
  function brassSet(st) { brass.want = st || { present: false }; }

  // At boot: an errand waiting or running puts him straight back where he was, with no walk in.
  function brassRestore(st) {
    brassSet(st);
    if (!st?.present || !["waiting", "working"].includes(st.stage)) {
      // no job on: he is somewhere in the vault, about his own business
      newWander();
      const at = brass.wanderAt;
      if (!at) return false;
      [brass.x, brass.y] = at; brass.rx = brass.x; brass.ry = brass.y;
      brass.hidden = false; brass.alpha = 1; brass.goal = "wander"; brass.arrived = true;
      brass.restUntil = now() + (20 + Math.random() * 60) * 1000;
      brass.tinker = dist(at, stationOf("outbox") || [-9, -9]) <= 1;
      return true;
    }
    const at = standCell(st.goes);
    if (!at) return false;
    [brass.x, brass.y] = at; brass.rx = brass.x; brass.ry = brass.y;
    brass.hidden = false; brass.alpha = 1; brass.goal = st.goes; brass.arrived = true; brass.tinker = st.goes === "outbox";
    return true;
  }

  // For Tally's game_state: one short phrase, only while he is in.
  function brassText() {
    if (brass.hidden) return "";
    const w = brass.want;
    if (brass.goal === "wander") {
      const r = roomAt(brass.x, brass.y);
      return `Brass is between jobs, pottering about ${r ? r.name : "the vault"}${brass.tinker ? " at his Outbox table" : ""}.`;
    }
    if (brass.goal === "door" || !w?.present) return "Brass is leaving by the front door.";
    if (brass.pinned === w.errand) return "Brass has pinned his report and is moving on.";
    return (brass.arrived ? w.say : w.going) || "";
  }

  return {
    cat,
    imp,
    name: NAME,
    brass,
    entities() { return [cat, ...(imp.hidden ? [] : [imp]), ...(brass.hidden ? [] : [brass])]; },
    tick(busy) { const t = now(); catStep(t, busy); impStep(t); brassStep(t); },
    errandOut(e) { out.push(e); },
    errandBack(e) { back.push(e); },
    brassSet,
    brassRestore,
    brassText,
    brassNear: (x, y) => !brass.hidden && dist([brass.x, brass.y], [x, y]) <= 1,
    brassAround: () => around(brass.x, brass.y, open),
    at(x, y) {
      if (cat.x === x && cat.y === y) return "cat";
      if (!imp.hidden && imp.x === x && imp.y === y) return "imp";
      if (!brass.hidden && brass.x === x && brass.y === y) return "brass";
      return "";
    },
    lookAtBrass() {
      const d = world.creatures?.brass?.desc || "Brass, the Artificer.";
      const t = brassText();
      log(`${d}${t ? ` ${t}` : ""}`);
    },
    apply,
    snapshot,
    restore,
    whereWord,
    doingWord,
    hungry,
    next: (x, y) => dist([cat.x, cat.y], [x, y]) <= 1,
    around: () => around(cat.x, cat.y, open),
    lookAtCat() {
      const where = whereWord();
      const fond = !guest && cat.fondness.owner ? ` It ${cat.fondness.owner} you.` : "";
      log(`${def.desc || NAME} It is ${doingWord()}${where ? `, ${where}` : ""}. ${pick(CAT_LOOKS)}${fond}`);
    },
    // how it is with Tally and OWNER, in words, for Tally's game_state
    fondnessText: () => cat.fondnessText,
    lookAtImp() {
      log(imp.carrying ? "A small soot-coloured imp with a rolled-up report under one arm. It is in a hurry." : "A small soot-coloured imp, the vault's errand runner. It is in a hurry.");
    },
  };
}
