// The vault's other residents, scripted in the browser: no model calls, so they cost nothing.
//
// The cat wanders between warm and quiet places (braziers, the fireplace, the pool, the desk),
// naps there, and sits on the ledger when it is busiest. It never stops in or next to a doorway,
// and it never blocks anyone: Tally and OWNER walk straight through it.
//
// The imp is the errand runner. When an errand starts it leaves the desk and runs out of the
// vault; when the errand is done it comes back in with the report and pins it to the notice board.

const STEPS4 = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const WARM = new Set(["brazier", "fireplace", "fireplace_side", "pool", "desk", "desk_side", "candle", "couch"]);
const CAT_STEP_MS = 520, IMP_STEP_MS = 190;

const CAT_LOOKS = [
  "The cat looks back at you, slowly closes its eyes, and opens them again. You have been approved of.",
  "The cat is not interested in you right now. It is interested in a speck of light on the floor.",
  "The cat flicks one ear at you, which is as much as you are getting.",
  "The cat stares at you without blinking. You blink first.",
];
const CAT_PETS = [
  "You scratch the cat behind the ears. It leans into your hand and purrs like a small engine.",
  "The cat allows exactly two strokes, then walks off as if nothing happened.",
  "The cat rolls over to show its belly. It is a trap; you know it is a trap.",
  "The cat butts its head against your hand and leaves black hair on your sleeve.",
];

export function createDenizens(world, api) {
  const { tileAt, log, now = () => performance.now() } = api;
  const map = world.map;
  const cell = (x, y) => tileAt(x, y);
  const isDoor = (x, y) => cell(x, y)?.id === "door";
  const nearDoor = (x, y) => isDoor(x, y) || STEPS4.some(([dx, dy]) => isDoor(x + dx, y + dy));
  const open = (x, y) => { const t = cell(x, y); return !!(t && t.def && !t.def.solid); };
  const fixedSpots = [world.spawn, world.exit, world.tally].filter(Boolean).map(([x, y]) => `${x},${y}`);

  // Places the cat likes: open floor beside something warm or soft, away from doorways.
  const liked = [];
  for (let y = 0; y < map.length; y++) {
    for (let x = 0; x < map[y].length; x++) {
      if (!open(x, y) || nearDoor(x, y) || fixedSpots.includes(`${x},${y}`)) continue;
      const here = cell(x, y)?.id;
      const warmBy = STEPS4.some(([dx, dy]) => WARM.has(cell(x + dx, y + dy)?.id));
      if (warmBy || cell(x, y)?.def?.use) liked.push([x, y, here]);
    }
  }
  const deskCells = [];
  for (let y = 0; y < map.length; y++) for (let x = 0; x < map[y].length; x++) if (cell(x, y)?.id === "desk") deskCells.push([x, y]);

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
  const start = liked.length ? pick(liked) : world.spawn;
  const cat = { kind: "cat", x: start[0], y: start[1], rx: start[0], ry: start[1], path: [], restUntil: now() + 15000,
                sleeping: false, onDesk: false, nextStep: 0 };
  const imp = { kind: "imp", x: 0, y: 0, rx: 0, ry: 0, path: [], hidden: true, alpha: 0, job: null, nextStep: 0, carrying: false };
  const out = [];  // errands the imp is out on
  const back = []; // reports waiting to be brought in

  // ---------- the cat ----------

  function catChoose(busy) {
    if (busy && deskCells.length) {
      // the ledger is busiest: go and sit on it
      const desk = deskCells[Math.floor(deskCells.length / 2)];
      const beside = STEPS4.map(([dx, dy]) => [desk[0] + dx, desk[1] + dy]).filter(([x, y]) => open(x, y));
      const path = bfs([cat.x, cat.y], beside, open);
      if (path) { cat.path = path; cat.goal = { desk }; return; }
    }
    const choices = liked.filter(([x, y]) => Math.abs(x - cat.x) + Math.abs(y - cat.y) > 3);
    const target = choices.length ? pick(choices) : null;
    if (!target) { cat.restUntil = now() + 30000; return; }
    const path = bfs([cat.x, cat.y], [[target[0], target[1]]], open);
    if (path) { cat.path = path; cat.goal = { at: target }; }
    else cat.restUntil = now() + 20000;
  }

  function catStep(t, busy) {
    if (cat.path.length) {
      if (t < cat.nextStep) return;
      cat.nextStep = t + CAT_STEP_MS * (0.8 + Math.random() * 0.5);
      const [nx, ny] = cat.path.shift();
      cat.x = nx; cat.y = ny;
      if (!cat.path.length) {
        if (cat.goal?.desk) { [cat.x, cat.y] = cat.goal.desk; cat.onDesk = true; }
        // never settle in or by a doorway: walk on to the nearest liked place instead
        if (!cat.onDesk && nearDoor(cat.x, cat.y)) { catChoose(false); return; }
        const warm = STEPS4.some(([dx, dy]) => WARM.has(cell(cat.x + dx, cat.y + dy)?.id));
        cat.sleeping = warm && Math.random() < 0.6;
        cat.restUntil = t + (cat.sleeping ? 90000 + Math.random() * 120000 : 25000 + Math.random() * 50000);
        if (cat.onDesk) cat.restUntil = t + 60000;
      }
      return;
    }
    if (t < cat.restUntil) return;
    if (cat.onDesk) {
      if (busy) { cat.restUntil = t + 30000; return; } // still busy: stay on the ledger
      const off = STEPS4.map(([dx, dy]) => [cat.x + dx, cat.y + dy]).find(([x, y]) => open(x, y));
      if (off) [cat.x, cat.y] = off;
      cat.onDesk = false;
    }
    cat.sleeping = false;
    catChoose(busy);
  }

  // ---------- the imp ----------

  const exitIn = () => {
    // the floor cell just inside the way out
    const [ex, ey] = world.exit;
    return STEPS4.map(([dx, dy]) => [ex + dx, ey + dy]).find(([x, y]) => open(x, y)) || world.spawn;
  };
  const boardSide = () => {
    for (let y = 0; y < map.length; y++) {
      for (let x = 0; x < map[y].length; x++) {
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

  return {
    cat,
    imp,
    entities() { return imp.hidden ? [cat] : [cat, imp]; },
    tick(busy) { const t = now(); catStep(t, busy); impStep(t); },
    errandOut(e) { out.push(e); },
    errandBack(e) { back.push(e); },
    at(x, y) {
      if (cat.x === x && cat.y === y) return "cat";
      if (!imp.hidden && imp.x === x && imp.y === y) return "imp";
      return "";
    },
    lookAtCat() {
      const doing = cat.onDesk ? "It is sitting squarely on the open ledger." : cat.sleeping ? "It is asleep, curled up nose to tail." : cat.path.length ? "It is on its way somewhere, with purpose." : "It is sitting and watching nothing in particular.";
      log(`The vault cat: black as the walls, with amber eyes. It has no job and no name yet. ${doing} ${pick(CAT_LOOKS)}`);
    },
    petCat() { log(pick(CAT_PETS)); cat.sleeping = false; },
    lookAtImp() {
      log(imp.carrying ? "A small soot-coloured imp with a rolled-up report under one arm. It is in a hurry." : "A small soot-coloured imp, the vault's errand runner. It is in a hurry.");
    },
  };
}
