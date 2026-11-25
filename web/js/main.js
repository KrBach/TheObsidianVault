import { GlyphRenderer } from "./renderer.js";
import { IsoRenderer } from "./iso.js";
import { Portrait } from "./portrait.js";
import { talk, resetConversation } from "./talk.js";
import { renderMarkdown } from "./markdown.js";
import * as ledger from "./ledger.js";
import * as board from "./board.js";

const $ = (id) => document.getElementById(id);
const logEl = $("log"), form = $("talk"), input = $("say");

const SHELF_COLOURS = ["#c98a5a", "#7fb07a", "#c0a050", "#6a9ad0", "#b07ac0", "#d07070", "#70c0c0", "#a0a0a0"];

let portraits = null;
const bottom = document.getElementById("bottom");

const state = {
  world: null,
  player: { kind: "player", x: 0, y: 0, rx: 0, ry: 0 },
  tally: { kind: "tally", x: 0, y: 0, rx: 0, ry: 0, alpha: 1, state: "idle", stateSince: 0 },
  path: [], // queued steps from a click
  onArrive: null, // what to do when the path ends
  view: "iso",
  talking: false,
  busy: false,
  shelfAt: new Map(), // "x,y" -> shelf info
};

// ---------- log ----------

function log(text, cls = "sys") {
  // Repeats (bumping the same candle five times) fold into one line with a count.
  const last = logEl.lastElementChild;
  if (last && last.className === cls && last.dataset.text === text) {
    last.dataset.count = String(Number(last.dataset.count || 1) + 1);
    last.textContent = `${text} (x${last.dataset.count})`;
    return last;
  }
  const div = document.createElement("div");
  div.dataset.text = text;
  div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function logLine(who, cls) {
  const div = document.createElement("div");
  div.className = cls;
  const name = document.createElement("span");
  name.className = "who";
  name.textContent = `${who}: `;
  const body = document.createElement("span");
  div.append(name, body);
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return body;
}

// ---------- world ----------

const tileAt = (x, y) => {
  const { map, legend, tiles } = state.world;
  const ch = map[y]?.[x];
  return ch === undefined ? null : { ch, id: legend[ch], def: tiles[legend[ch]] };
};

function roomAt(x, y) {
  return state.world.rooms.find(({ rect: [rx, ry, w, h] }) => x >= rx && x < rx + w && y >= ry && y < ry + h);
}

// Group neighbouring shelf cells into shelves and label them with the vault's theme folders.
function labelShelves() {
  const { map, shelves } = state.world;
  const seen = new Set();
  const themes = shelves.filter((s) => !["archive", "templates"].includes(s.name) && !/output/i.test(s.name));
  const archive = shelves.find((s) => s.name === "archive");
  const outputs = shelves.find((s) => /output/i.test(s.name));
  let n = 0;
  for (let y = 0; y < map.length; y++) {
    for (let x = 0; x < map[y].length; x++) {
      const ch = map[y][x];
      if (ch !== "S" && ch !== "A") continue;
      if (seen.has(`${x},${y}`)) continue;
      // flood along the row: shelves are horizontal runs
      let info;
      if (ch === "S" && roomAt(x, y)?.id === "scriptorium") {
        // the Scriptorium's shelves hold what has been written up
        info = outputs ? { label: outputs.name, notes: outputs.notes, colour: "#8a6a3a" } : { label: "finished pages", notes: 0, colour: "#8a6a3a" };
      } else if (ch === "S") {
        const theme = themes[n];
        info = theme
          ? { label: theme.name, notes: theme.notes, colour: SHELF_COLOURS[n % SHELF_COLOURS.length] }
          : { label: "an empty shelf", notes: 0, colour: "#4a3a3a" };
        n++;
      } else {
        info = archive ? { label: "archive", notes: archive.notes } : { label: "archive", notes: 0 };
      }
      for (let xx = x; map[y][xx] === ch; xx++) {
        seen.add(`${xx},${y}`);
        state.shelfAt.set(`${xx},${y}`, info);
      }
    }
  }
  state.world.shelfColour = (x, y) => state.shelfAt.get(`${x},${y}`)?.colour;
}

function describe(x, y) {
  const t = tileAt(x, y);
  if (!t || !t.def) return null;
  if (t.id === "shelf" || t.id === "archive_shelf") {
    const s = state.shelfAt.get(`${x},${y}`);
    if (!s) return t.def.name;
    return s.notes ? `${t.def.name}: ${s.label} (${s.notes} notes)` : `${t.def.name}: ${s.label}`;
  }
  const name = t.def.name[0].toUpperCase() + t.def.name.slice(1);
  return t.def.desc ? `${name}. ${t.def.desc}` : name;
}

// Close enough to talk: next to Tally, or across the desk.
const TALK_RANGE = 2;
function inTalkRange(a, b) {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)) <= TALK_RANGE;
}

function talkSpots() {
  const spots = [];
  for (let dy = -TALK_RANGE; dy <= TALK_RANGE; dy++) {
    for (let dx = -TALK_RANGE; dx <= TALK_RANGE; dx++) {
      const x = state.tally.x + dx, y = state.tally.y + dy;
      if (walkable(x, y)) spots.push([x, y]);
    }
  }
  return spots;
}

// What you notice looking around a room: its description, the things in it, and who is there.
const PLAIN = new Set(["floor", "floor_glint", "wall", "door", "doorway"]);
function lookAround(room) {
  if (!room) return log("A passage between rooms. Bare stone, and the hum of the vault.");
  log(room.name, "room-name");
  if (room.desc) log(room.desc);
  const [rx, ry, w, h] = room.rect;
  const things = new Map();
  for (let y = ry; y < ry + h; y++) for (let x = rx; x < rx + w; x++) {
    const t = tileAt(x, y);
    if (!t?.def || PLAIN.has(t.id)) continue;
    const label = t.id.includes("shelf") && state.shelfAt.get(`${x},${y}`)?.label;
    const name = label ? `a shelf of ${label}` : /^(the |tally's )/i.test(t.def.name) ? t.def.name : `${/^[aeiou]/i.test(t.def.name) ? "an" : "a"} ${t.def.name}`;
    things.set(name, (things.get(name) || 0) + 1);
  }
  const list = [...things.keys()].slice(0, 14);
  if (list.length) log(`You see ${list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list.at(-1)}` : list[0]}.`);
  if (roomAt(state.tally.x, state.tally.y) === room) log(`Tally is here, ${tallyNow()}.`, "action");
}

// What Tally is doing, told to Kristian (doingText() is the same, told to Tally).
function tallyNow() {
  const t = state.tally, d = t.doing || {};
  if (d.kind === "follow") return "walking with you";
  if (t.path?.length) return d.kind === "go" && d.name ? `on their way, ${d.name}` : "walking";
  if (t.pose) return poseText(t);
  if (t.x === state.world.tally[0] && t.y === state.world.tally[1]) return "at their desk in the Hearth";
  const r = roomAt(t.x, t.y);
  return `standing in ${r ? r.name : "the vault"}`;
}

function lookAtTally() {
  const t = state.tally;
  log(`Tally: the vault's archivist, in a plain dark coat, ink on their fingers, the ledger under one arm. ` +
    `Tally is ${tallyNow()}.`);
}

// ---------- walking ----------

const walkable = (x, y) => {
  const t = tileAt(x, y);
  return !!(t && t.def && !t.def.solid) && !(x === state.tally.x && y === state.tally.y);
};

const STEPS4 = [[1, 0], [-1, 0], [0, 1], [0, -1]];

// Shortest path (straight steps) from the player to any of the goal cells.
function findPath(goals) {
  return bfs([state.player.x, state.player.y], goals, walkable);
}

// Shortest path from `start` to any goal, stepping only on cells where passable(x, y).
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

const neighbours = (x, y) => STEPS4.map(([dx, dy]) => [x + dx, y + dy]).filter(([nx, ny]) => walkable(nx, ny));

// ---------- furniture and resting ----------
// A piece of furniture says what you can do on it (tiles.json "use": poses sit, lie or walk, and the
// height of its surface). Plain floor takes sit and lie too. Each figure has a pose (null when
// standing); stopping on furniture takes its first pose, and any step clears it.

const FLOOR_REST = new Set(["floor", "floor_glint", "mat", "compass_rose", "papers"]);
const POSE_WORD = { sit: "sitting", lie: "lying", walk: "walking in place" };

function useAt(x, y) {
  const t = tileAt(x, y);
  return t?.def?.use ? t : null;
}

function canPose(x, y, how) {
  const t = tileAt(x, y);
  if (!t?.def) return false;
  if (t.def.use) return t.def.use.poses.includes(how);
  return how !== "walk" && FLOOR_REST.has(t.id);
}

function poseOn(x, y, how) {
  const t = tileAt(x, y);
  return { how, h: t?.def?.use ? t.def.use.h || 0 : 0, floor: !t?.def?.use };
}

function restPlace(x, y) {
  const spot = (state.world.spots || []).find((s) => s.at[0] === x && s.at[1] === y);
  const t = tileAt(x, y);
  const room = roomAt(x, y);
  if (t?.def?.use) return spot ? spot.name : `on a ${t.def.name} in ${room ? room.name : "the vault"}`;
  return `on the floor ${spot ? spot.name.replace(/^(at|in|on|by|among|along|sorting) /, "$1 ") : `in ${room ? room.name : "the vault"}`}`;
}

function poseText(e) {
  return e.pose ? `${POSE_WORD[e.pose.how]} ${restPlace(e.x, e.y)}` : "";
}

// Tiles where a pose fits, optionally of one furniture kind ("floor" for bare floor).
function restTiles(how, item = "") {
  const out = [], map = state.world.map;
  for (let y = 0; y < map.length; y++) for (let x = 0; x < map[y].length; x++) {
    const t = tileAt(x, y);
    if (!t?.def || !canPose(x, y, how)) continue;
    const match = item === "floor" ? !t.def.use : item ? t.id.startsWith(item) : !!t.def.use;
    if (!match) continue;
    out.push([x, y]);
  }
  return out;
}

function setPose(e, how) {
  const before = e.pose;
  e.pose = how ? poseOn(e.x, e.y, how) : null;
  e.poseAt = `${e.x},${e.y}`;
  if (e === state.player) {
    if (how) log(`You ${how === "walk" ? "step onto the belt and walk, going nowhere" : how === "lie" ? "lie down" : "sit down"} ${restPlace(e.x, e.y)}.`, "action");
    else if (before) log("You get up.", "action");
  }
}

// Kristian's keys: x to sit (here, or the nearest seat), z to lie down; again to get up.
function restKey(how) {
  const p = state.player;
  if (state.path.length) return;
  if (p.pose) { p.declined = `${p.x},${p.y}`; return setPose(p, null); }
  if (canPose(p.x, p.y, how)) return setPose(p, how);
  const tile = tileAt(p.x, p.y);
  if (tile?.def?.use && how === "sit" && tile.def.use.poses[0]) return setPose(p, tile.def.use.poses[0]);
  const near = restTiles(how).filter(([x, y]) => !(x === state.tally.x && y === state.tally.y));
  const path = findPath(near);
  if (path === null || path.length > 12) return log(how === "lie" ? "There is nowhere to lie down here." : "There is nowhere to sit here.");
  if (state.talking) stopTalking();
  p.wantPose = how;
  state.path = path;
  state.onArrive = null;
}

function walkTo(x, y) {
  if (state.talking) stopTalking();
  const t = tileAt(x, y);
  if (!t || !t.def) return;
  const isTally = (x === state.tally.x && y === state.tally.y) || t.id?.startsWith("desk");
  state.goingToTally = isTally; // Tally waits for you instead of wandering off
  let goals, onArrive = null;
  if (isTally) {
    goals = talkSpots();
    onArrive = () => { state.goingToTally = false; startTalking(); };
  } else if (t.def.solid) {
    goals = neighbours(x, y);
    if (t.id === "bell") onArrive = ringBell;
    else if (t.id !== "wall") onArrive = () => log(describe(x, y));
  } else {
    goals = [[x, y]];
  }
  const path = findPath(goals);
  if (path === null) return log("You can't find a way there.");
  state.path = path;
  state.onArrive = onArrive;
  if (!path.length && onArrive) { state.onArrive = null; onArrive(); }
}

function stepAlongPath() {
  if (!state.path.length) return;
  const [nx, ny] = state.path.shift();
  move(nx - state.player.x, ny - state.player.y);
  if (!state.path.length && state.onArrive) {
    const f = state.onArrive;
    state.onArrive = null;
    f();
  }
}

// ---------- Tally moves ----------
// Tally always has something they are doing, and why (state.tally.doing):
//   round   no choice in force. With ticks on, Tally stays put until the next tick chooses; with
//           ticks off, the game walks Tally between world.tally_posts and says it is not their choice
//   go      walking to a place they chose, then staying there
//   stay    staying where they are
//   follow  walking a step or two behind Kristian
// "go", "stay" and "follow" come from Tally: from the game tools in a reply, or from a tick.
// They hold until the next tick asks Tally again (or longer, for a firm promise in minutes);
// meanwhile the round does not move Tally. If ticks are off or paused, they lapse after a while.

const TALLY_STEP_MS = 380;
const NOTICE_RANGE = 3;
const SOFT_HOLD_MS = 20 * 60 * 1000; // a promise lapses after this if no tick comes to renew it

function tallyFree(x, y) {
  const t = tileAt(x, y);
  return !!(t && t.def && !t.def.solid) && !(x === state.player.x && y === state.player.y);
}

const DEFAULT_ROUND = "the game's default round (ticks are off, so this route is not your choice)";
const ticksOn = () => (Number(state.world?.status?.tick_minutes) || 0) > 0;

function setDoing(kind, why = "", extra = {}) {
  const now = performance.now();
  state.tally.doing = { kind, why, since: now, softUntil: now + SOFT_HOLD_MS, firmUntil: 0, ...extra };
}

function tallyHeld() {
  const d = state.tally.doing;
  if (!d || d.kind === "round") return false;
  const now = performance.now();
  if (now > Math.max(d.softUntil || 0, d.firmUntil || 0)) {
    if (d.kind === "follow") log("Tally lets you go on alone.", "action");
    setDoing("round", ticksOn() ? "your last choice ran out; staying put until you choose again" : DEFAULT_ROUND);
    return false;
  }
  return true;
}

function tallyShouldStay() {
  const p = state.player, t = state.tally;
  const close = Math.max(Math.abs(p.x - t.x), Math.abs(p.y - t.y)) <= NOTICE_RANGE;
  // A trip Tally chose goes ahead even with Kristian close or still at the desk: Tally said so, and goes.
  if (t.doing?.kind === "go" && t.path?.length && !state.busy) return false;
  return state.busy || state.talking || close || (state.goingToTally && !!state.onArrive);
}

function stepTally(nx, ny) {
  const t = state.tally;
  const before = roomAt(t.x, t.y);
  t.x = nx; t.y = ny;
  const after = roomAt(nx, ny);
  const mine = roomAt(state.player.x, state.player.y);
  if (t.doing?.kind !== "follow" && after !== before && mine && (before === mine || after === mine)) {
    log(after === mine ? `Tally walks in.` : `Tally heads off toward ${roomOf(t.goal)?.name || "another room"}.`, "action");
  }
}

function tallyTick() {
  const t = state.tally, posts = state.world.tally_posts || [];
  const now = performance.now();
  const held = tallyHeld();
  if (t.doing?.kind === "follow") return followStep();
  if (tallyShouldStay()) {
    // look up and wait a little after Kristian leaves, rather than rushing off
    t.waitUntil = Math.max(t.waitUntil || 0, now + 8000);
    return;
  }
  if (t.path?.length) {
    const [nx, ny] = t.path[0];
    if (!tallyFree(nx, ny)) { // someone is in the way: re-plan next step
      t.path = t.doing?.kind === "go" ? bfs([t.x, t.y], [t.goal.at], tallyFree) : null;
      return;
    }
    t.path.shift();
    stepTally(nx, ny);
    if (!t.path.length) {
      const [a, b] = t.goal.dwell || [20, 60];
      t.waitUntil = now + (a + Math.random() * (b - a)) * 1000;
      t.post = t.goal;
      t.arrivedAt = now;
    }
    return;
  }
  // A small round Tally planned at the last tick: on to the next place once the dwell is over.
  if (t.plan?.length && now >= (t.waitUntil || 0) && !((t.doing?.firmUntil || 0) > now)) {
    const next = t.plan.shift();
    if (sendTally(next.at, next.name, next.room, next.why || "the next stop on the round you planned", 0, true)) {
      if (t.path.length) log(`Tally moves on, ${next.name}.`, "action");
    }
    return;
  }
  // With ticks on, every move is Tally's own choice: no scripted round.
  if (held || ticksOn() || !posts.length || now < (t.waitUntil || 0)) return;
  const choices = posts.filter((p) => p !== t.post);
  // the desk is home: come back to it more often than anywhere else
  const goal = Math.random() < 0.4 && t.post !== posts[0] ? posts[0] : choices[Math.floor(Math.random() * choices.length)];
  const path = bfs([t.x, t.y], [goal.at], tallyFree);
  if (path === null) { t.waitUntil = now + 5000; return; }
  t.goal = goal;
  t.path = path;
  t.doing.why = DEFAULT_ROUND;
}

// Walking with Kristian: stay a step or two behind him.
function followStep() {
  const t = state.tally, p = state.player;
  if (state.busy) return;
  if (Math.max(Math.abs(p.x - t.x), Math.abs(p.y - t.y)) <= 2) return;
  const goals = [];
  for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
    if ((dx || dy) && tallyFree(p.x + dx, p.y + dy)) goals.push([p.x + dx, p.y + dy]);
  }
  const path = bfs([t.x, t.y], goals, tallyFree);
  if (path?.length) stepTally(...path[0]);
}

// Send Tally to a place: from a reply's go_to, a tick, or the bell.
function sendTally(at, name, room, why, firmMs = 0, keepPlan = false) {
  const t = state.tally;
  if (!keepPlan) t.plan = []; // a new choice replaces the rest of an old round
  const path = bfs([t.x, t.y], [at], tallyFree);
  if (path === null) { log(`Tally can't find a way ${name}.`, "action"); return false; }
  setDoing("go", why, { name, firmUntil: firmMs ? performance.now() + firmMs : 0 });
  t.goal = { at, room, name, dwell: [60, 120] };
  t.path = path;
  t.arrivedAt = path.length ? 0 : performance.now();
  return true;
}

// Tally sits, lies or walks in place: at a named place, on the nearest piece that suits, or on the floor.
function settleTally(ev, firm = 0) {
  const t = state.tally, how = ev.how || "sit", item = ev.item || "";
  const notPlayer = ([x, y]) => !(x === state.player.x && y === state.player.y);
  let goal = null;
  if (ev.at) {
    const [px, py] = ev.at;
    const near = restTiles(how, item).filter(notPlayer)
      .filter(([x, y]) => Math.max(Math.abs(x - px), Math.abs(y - py)) <= 2)
      .sort((a, b) => Math.hypot(a[0] - px, a[1] - py) - Math.hypot(b[0] - px, b[1] - py));
    goal = near[0] || (canPose(px, py, how) && item !== "floor" ? [px, py] : null) || (how !== "walk" ? [px, py] : null);
  } else if (item === "floor" || (!item && !restTiles(how).length)) {
    goal = [t.x, t.y];
  } else {
    const path = bfs([t.x, t.y], restTiles(how, item).filter(notPlayer), tallyFree);
    goal = path === null ? null : path.length ? path[path.length - 1] : [t.x, t.y];
  }
  if (!goal) { log("Tally looks about for somewhere to settle, and finds nothing free.", "action"); return; }
  const name = restPlace(goal[0], goal[1]);
  t.wantPose = canPose(goal[0], goal[1], how) ? how : (how === "walk" ? null : "sit");
  if (goal[0] === t.x && goal[1] === t.y) {
    t.path = null;
    t.pose = null; // re-take the pose below, on the next frame
    setDoing("go", ev.why, { name, firmUntil: firm ? performance.now() + firm : 0 });
    return;
  }
  if (sendTally(goal, name, roomAt(...goal)?.id || "", ev.why, firm)) {
    t.wantPose = canPose(goal[0], goal[1], how) ? how : "sit";
    if (t.path.length) log(`Tally goes ${how === "lie" ? "to lie down" : how === "walk" ? "to the treadmill" : "to sit"}, ${name}.`, "action");
  }
}

// What Tally said they would do, from the game tools in a reply: go, stay, follow or wander.
function applyIntent(ev) {
  const t = state.tally;
  const firm = (Number(ev.minutes) || 0) * 60000;
  if (ev.kind === "go" && ev.at) {
    if (sendTally(ev.at, ev.name, ev.room, ev.why, firm) && t.path.length) log(`Tally sets off, ${ev.name}.`, "action");
  } else if (ev.kind === "settle") {
    settleTally(ev, firm);
  } else if (ev.kind === "stay") {
    t.path = null;
    t.plan = [];
    setDoing("stay", ev.why, { firmUntil: firm ? performance.now() + firm : 0 });
  } else if (ev.kind === "follow") {
    t.path = null;
    t.plan = [];
    setDoing("follow", ev.why, { firmUntil: firm ? performance.now() + firm : 0 });
    log("Tally falls in beside you.", "action");
  } else if (ev.kind === "free") {
    t.plan = [];
    setDoing("round", ticksOn() ? "free to choose again; you will decide where to go in a moment" : DEFAULT_ROUND);
    t.waitUntil = performance.now() + 20000;
    if (ticksOn()) setTimeout(tallyChooses, 20000); // choose soon, rather than at the next regular tick
  }
}

// Where Tally is, what they are doing and why: for the talk's game_state line and for ticks.
function doingText() {
  const t = state.tally, d = t.doing || { kind: "round" };
  if (d.kind === "follow") return "walking with Kristian";
  if (d.kind === "go") return t.path?.length ? `on your way ${d.name}` : (t.pose ? poseText(t) : `staying ${d.name}`);
  if (d.kind === "stay") return t.pose ? `${poseText(t)}, as you chose` : "staying here, as you chose";
  if (ticksOn()) return "staying put; you have not chosen anywhere new yet";
  return t.path?.length ? "walked by the game's default round" : "paused on the game's default round";
}

function promiseText() {
  const d = state.tally.doing;
  return ` You are ${doingText()}${d?.why ? ` (why: ${d.why})` : ""}.`;
}

// Tally moved furniture: load the map again and keep everyone on their feet.
async function reloadWorld() {
  const fresh = await (await fetch("/api/world")).json();
  const w = state.world;
  w.map = fresh.map; w.spots = fresh.spots; w.tally_posts = fresh.tally_posts;
  for (const r of Object.values(window.vault?.renderers || {})) if (r.findLights) r.lights = r.findLights();
  for (const e of [state.player, state.tally]) {
    if (tileAt(e.x, e.y)?.def?.solid) {
      const spot = nearestFloor(e.x, e.y);
      if (spot) [e.x, e.y] = spot;
    }
  }
  log("Tally rearranges the vault a little.", "action");
}

function nearestFloor(x, y) {
  for (let r = 1; r < 6; r++) {
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      const t = tileAt(x + dx, y + dy);
      if (t?.def && !t.def.solid) return [x + dx, y + dy];
    }
  }
  return null;
}

// ---------- Tally's tick ----------
// Every few minutes (status.tick_minutes) the server asks Tally, in one small model call,
// whether to go somewhere or say something, telling them where they are and why. The answer
// names a spot (world.spots), or "stay" to keep doing what they are doing.
// No ticks while the tab is hidden, while talking, or after 30 minutes without input.

const IDLE_STOP_MS = 30 * 60 * 1000;
let lastInput = performance.now();
const touched = () => { lastInput = performance.now(); };

async function tallyChooses() {
  const t = state.tally, now = performance.now();
  if (document.hidden || state.busy || state.talking || state.ticking) return;
  if (t.doing?.kind !== "follow" && t.path?.length) return;
  if ((t.doing?.firmUntil || 0) > now) return; // a firm promise holds
  if (now - lastInput > IDLE_STOP_MS) return;
  state.ticking = true;
  let res;
  try {
    const r = await fetch("/api/tick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tally: { x: t.x, y: t.y }, player: { x: state.player.x, y: state.player.y },
        here_minutes: (now - (t.arrivedAt || state.bootedAt || now)) / 60000,
        doing: doingText(), why: t.doing?.why || "",
        recent: state.lastSaid || "", recent_reply: state.lastReply || "",
      }),
    });
    res = await r.json();
  } catch (_) {
    res = null;
  } finally {
    state.ticking = false;
  }
  if (!res || state.busy || state.talking) return;
  const mine = roomAt(state.player.x, state.player.y), theirs = roomAt(t.x, t.y);
  if (res.say) {
    const line = logLine(mine === theirs ? "Tally" : `Tally (in ${theirs ? theirs.name : "the vault"})`, "tally");
    line.textContent = res.say;
  }
  const plan = Array.isArray(res.plan) ? res.plan : [];
  if (res.rest && (res.go === "stay" || !res.at)) {
    settleTally({ how: res.rest, why: res.why || "settling in" });
    t.plan = plan;
    return;
  }
  if (res.go === "stay" || !res.at) {
    // keep doing what Tally was doing, renewed until the next tick
    const d = t.doing;
    if (!d || d.kind === "round") setDoing("stay", res.why || "chose to stay a while");
    else { d.softUntil = performance.now() + SOFT_HOLD_MS; if (res.why) d.why = res.why; }
    t.plan = plan;
    if (plan.length) t.waitUntil = performance.now() + (60 + Math.random() * 60) * 1000;
    return;
  }
  if (res.at[0] === t.x && res.at[1] === t.y) { setDoing("go", res.why, { name: res.name }); t.plan = plan; return; }
  if (res.rest) {
    settleTally({ how: res.rest, why: res.why || "", at: res.at, name: res.name });
    t.plan = plan;
    return;
  }
  if (sendTally(res.at, res.name, res.room, res.why || "") && !res.say) log(`Tally sets off, ${res.name}.`, "action");
  t.plan = plan;
}

// ---------- Tally, kept across sessions ----------
// The game saves where Tally is (cell, what they are doing and why, pose, the rest of a plan) to the
// server every few seconds when it changes, and once more when the page closes. The next time the
// game opens, Tally is where they were, still doing what they chose.

function tallySnapshot() {
  const t = state.tally, d = t.doing || {};
  const firmLeft = (d.firmUntil || 0) - performance.now();
  return {
    x: t.x, y: t.y, pose: t.pose?.how || null,
    doing: { kind: d.kind || "round", why: d.why || "", name: d.name || "",
      firm_until: firmLeft > 0 ? Date.now() / 1000 + firmLeft / 1000 : 0 },
    goal: d.kind === "go" && t.goal ? { at: t.goal.at, name: t.goal.name, room: t.goal.room } : null,
    plan: (t.plan || []).map((p) => ({ at: p.at, name: p.name, room: p.room, why: p.why })),
  };
}

let lastSaved = "";
function saveTally(beacon = false) {
  if (!state.world || !state.restored) return;
  const snap = tallySnapshot();
  const key = JSON.stringify({ ...snap, doing: { ...snap.doing, firm_until: Math.round(snap.doing.firm_until / 60) } });
  if (key === lastSaved) return;
  lastSaved = key;
  const body = JSON.stringify(snap);
  if (beacon && navigator.sendBeacon) navigator.sendBeacon("/api/tally", new Blob([body], { type: "application/json" }));
  else fetch("/api/tally", { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch(() => { lastSaved = ""; });
}

// Put Tally back where the last session left them. Returns true if there was somewhere to go back to.
function restoreTally(saved) {
  const t = state.tally;
  if (!saved || !tallyFree(saved.x, saved.y)) return false;
  t.x = t.rx = saved.x; t.y = t.ry = saved.y;
  const d = saved.doing || {};
  const firmLeft = d.firm_until ? d.firm_until * 1000 - Date.now() : 0;
  if (d.kind && d.kind !== "round" && d.kind !== "follow") { // walking with Kristian ends when he leaves
    setDoing(d.kind, d.why, { name: d.name, firmUntil: firmLeft > 0 ? performance.now() + firmLeft : 0 });
  } else {
    setDoing("round", ticksOn() ? "you stayed where you were when the game last closed; you have not chosen anywhere new yet" : DEFAULT_ROUND);
  }
  if (d.kind === "go" && saved.goal?.at && (saved.goal.at[0] !== t.x || saved.goal.at[1] !== t.y)) {
    const path = bfs([t.x, t.y], [saved.goal.at], tallyFree);
    if (path) { t.goal = { ...saved.goal, dwell: [60, 120] }; t.path = path; }
  } else if (saved.goal?.at) {
    t.goal = { ...saved.goal, dwell: [60, 120] };
  }
  t.plan = saved.plan || [];
  if (saved.pose && canPose(t.x, t.y, saved.pose)) { setPose(t, saved.pose); t.lastAuto = `${t.x},${t.y}`; }
  else if (!saved.pose) t.declined = `${t.x},${t.y}`; // standing where a chair is, as they were
  return true;
}

// The bell at the front of the Entrance Hall: Tally comes to you, wherever they are.
function ringBell() {
  const p = state.player, t = state.tally;
  log("You ring the little bell. Its note runs off down the corridors.", "action");
  const goals = [];
  for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
    if ((dx || dy) && tallyFree(p.x + dx, p.y + dy)) goals.push([p.x + dx, p.y + dy]);
  }
  const path = bfs([t.x, t.y], goals, tallyFree);
  if (path === null) return log("Nothing answers. Tally can't find a way to you.", "action");
  setDoing("go", "Kristian rang the bell for you", { name: "near Kristian, who rang the bell" });
  t.goal = { at: path.length ? path[path.length - 1] : [t.x, t.y], room: roomAt(p.x, p.y)?.id, name: "to the bell", dwell: [60, 120] };
  t.path = path;
  if (roomAt(t.x, t.y) !== roomAt(p.x, p.y)) log("Somewhere in the vault, Tally looks up and starts walking.", "action");
}

const roomOf = (post) => post && state.world.rooms.find((r) => r.id === post.room);

// ---------- actions ----------

function move(dx, dy) {
  const nx = state.player.x + dx, ny = state.player.y + dy;
  if (nx === state.tally.x && ny === state.tally.y) return startTalking();
  const t = tileAt(nx, ny);
  if (!t || !t.def) return;
  if (t.def.solid) {
    if (t.id === "bell") return ringBell();
    if (t.id !== "wall") log(describe(nx, ny));
    return;
  }
  const before = roomAt(state.player.x, state.player.y);
  state.player.x = nx;
  state.player.y = ny;
  const after = roomAt(nx, ny);
  if (after && after !== before) {
    // Say it once per visit, not every time you step back and forth across a doorway.
    const now = performance.now();
    const seen = (state.announced ||= {});
    if (!seen[after.name]) { log(`You enter ${after.name}.`, "room-name"); if (after.desc) log(after.desc); }
    else if (now - seen[after.name] > 30000) log(`You enter ${after.name}.`);
    seen[after.name] = now;
  }
  if (nx === state.world.exit[0] && ny === state.world.exit[1]) log("The way out. Close the tab to leave; Tally keeps the ledger.");
  if (t.id === "papers" || t.id === "mat") log(describe(nx, ny));
}

function startTalking() {
  if (!inTalkRange(state.player, state.tally)) {
    const r = roomAt(state.tally.x, state.tally.y);
    log(`Tally is in ${r ? r.name : "the vault"}. Walk up to Tally first.`);
    return;
  }
  if (!state.talking) log("Tally looks up from the ledger.", "action");
  state.talking = true;
  form.hidden = false;
  bottom.classList.add("talking");
  portraits?.player.setSpeaking(!state.busy);
  input.focus();
}

function stopTalking() {
  if (!state.talking) return;
  state.talking = false;
  form.hidden = true;
  bottom.classList.remove("talking");
  input.blur();
  log("You step back from the desk.", "action");
}

// Tally's animation state: "idle", "thinking" (working on a reply, or reading notes) or "speaking".
function setTallyState(st) {
  const t = performance.now() / 1000;
  if (state.tally.state !== st) {
    state.tally.state = st;
    state.tally.stateSince = t;
  }
  portraits?.tally.setState(st, t);
}

async function say(text) {
  if (state.busy) return;
  state.busy = true;
  input.disabled = true;
  portraits?.player.setSpeaking(false);
  portraits?.tally.setSpeaking(true);
  setTallyState("thinking");
  logLine("Kristian", "you").textContent = text;
  let body = logLine("Tally", "tally");
  body.classList.add("thinking");
  // Tool use shows as an action line. Keep the order true: action first if Tally has not
  // spoken yet, otherwise start a fresh Tally line after it.
  const onAction = (t) => {
    setTallyState("thinking");
    const line = log(`${t}.`, "action");
    if (!body.textContent) {
      body.parentElement.before(line);
    } else {
      body.classList.remove("thinking");
      body = logLine("Tally", "tally");
      body.classList.add("thinking");
    }
  };
  const room = roomAt(state.player.x, state.player.y);
  const tallyRoom = roomAt(state.tally.x, state.tally.y);
  const atDesk = state.tally.x === state.world.tally[0] && state.tally.y === state.world.tally[1];
  const where = `Kristian is ${state.player.pose ? poseText(state.player) : `standing in ${room ? room.name : "the vault"}`}, next to you. ` +
    `You are ${atDesk ? "at your desk in the Hearth" : `in ${tallyRoom ? tallyRoom.name : "the vault"}`}, ` +
    `at (${state.tally.x},${state.tally.y}).` + promiseText();
  state.lastSaid = text;
  try {
    await talk(text, where, {
      delta: (t) => {
        setTallyState("speaking");
        body.dataset.raw = (body.dataset.raw || "") + t;
        body.innerHTML = renderMarkdown(body.dataset.raw);
        logEl.scrollTop = logEl.scrollHeight;
      },
      action: onAction,
      error: (t) => log(t, "err"),
      intent: applyIntent,
      world: () => reloadWorld().catch((e) => log(`Could not reload the map: ${e.message}`, "err")),
    });
  } catch (e) {
    log(`Lost the connection to the vault: ${e.message}`, "err");
  }
  body.classList.remove("thinking");
  if (body.textContent) state.lastReply = body.dataset.raw || body.textContent;
  if (!body.textContent) body.parentElement.remove();
  setTallyState("idle");
  if (state.writes !== "off") {
    const before = state.waiting || 0;
    state.waiting = await ledger.refresh();
    if (state.waiting > before) log(`Tally sets ${state.waiting - before === 1 ? "a page" : "some pages"} on the desk for you to look at (see the ledger).`, "action");
  }
  state.busy = false;
  input.disabled = false;
  portraits?.tally.setSpeaking(false);
  portraits?.player.setSpeaking(state.talking);
  if (state.talking) input.focus();
}

// ---------- right-click menu and the game menu ----------
// Right-click on the map: what you can do with that spot. Whatever you learn goes to the text box below.

const theName = (t) => (/^(the |tally's )/i.test(t.def.name) ? t.def.name : `the ${t.def.name}`);
const POSE_VERB = { sit: "Sit", lie: "Lie down", walk: "Walk" };

function restAt(x, y, how) {
  const p = state.player;
  if (state.talking) stopTalking();
  if (p.x === x && p.y === y) { p.declined = null; return setPose(p, how); }
  if (x === state.tally.x && y === state.tally.y) return log("Tally is there.");
  const path = findPath([[x, y]]);
  if (path === null) return log("You can't find a way there.");
  p.wantPose = how;
  state.path = path;
  state.onArrive = null;
}

function ctxOptions(x, y) {
  const t = tileAt(x, y);
  if (!t?.def) return null;
  const p = state.player, room = roomAt(x, y), opts = [];
  const here = p.x === x && p.y === y;
  let head;
  if (x === state.tally.x && y === state.tally.y) {
    head = "Tally";
    opts.push(["Talk to Tally", () => walkTo(x, y)], ["Look at Tally", lookAtTally]);
  } else if (here) {
    head = p.pose ? `You, ${POSE_WORD[p.pose.how]}` : "You";
    if (p.pose) opts.push(["Get up", () => { p.declined = `${p.x},${p.y}`; setPose(p, null); }]);
  } else {
    head = t.id.includes("shelf") ? describe(x, y) : t.def.name;
  }
  const occupied = x === state.tally.x && y === state.tally.y;
  if (!occupied && (t.def.desc || t.id.includes("shelf"))) opts.push([`Inspect ${theName(t)}`, () => log(describe(x, y))]);
  if (t.id === "bell") opts.push(["Ring the bell", () => walkTo(x, y)]);
  if (t.id.startsWith("desk") && state.tally.x === state.world.tally[0] && state.tally.y === state.world.tally[1]) {
    opts.push(["Talk to Tally across the desk", () => walkTo(x, y)]);
  }
  if (t.def.use && !occupied) {
    for (const how of t.def.use.poses) {
      if (here && p.pose?.how === how) continue;
      const on = t.id === "hammock" ? "in" : "on";
      opts.push([`${POSE_VERB[how]} ${on} ${theName(t)}`, () => restAt(x, y, how)]);
    }
  } else if (!occupied && canPose(x, y, "sit")) {
    if (!(here && p.pose?.how === "sit")) opts.push([here ? "Sit down here" : "Sit on the floor there", () => restAt(x, y, "sit")]);
    if (!(here && p.pose?.how === "lie")) opts.push([here ? "Lie down here" : "Lie down there", () => restAt(x, y, "lie")]);
  }
  if (!here && !occupied && !t.def.solid) opts.push(["Walk here", () => walkTo(x, y)]);
  opts.push([room ? (room === roomAt(p.x, p.y) ? "Look around the room" : `Look around ${room.name}`) : "Look around", () => lookAround(room)]);
  return { head, opts };
}

const ctxEl = $("ctx");
function hideCtx() { ctxEl.hidden = true; ctxEl.replaceChildren(); }

function showCtx(clientX, clientY, x, y) {
  const menu = ctxOptions(x, y);
  if (!menu) return hideCtx();
  ctxEl.replaceChildren();
  const head = document.createElement("div");
  head.className = "ctx-head";
  head.textContent = menu.head;
  ctxEl.append(head);
  for (const [label, act] of menu.opts) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", () => { hideCtx(); act(); });
    ctxEl.append(b);
  }
  ctxEl.hidden = false;
  const box = $("view").getBoundingClientRect();
  const left = Math.min(clientX - box.left, box.width - ctxEl.offsetWidth - 4);
  const top = Math.min(clientY - box.top, box.height - ctxEl.offsetHeight - 4);
  ctxEl.style.left = `${Math.max(4, left)}px`;
  ctxEl.style.top = `${Math.max(4, top)}px`;
  ctxEl.querySelector("button")?.focus({ preventScroll: true });
}

const menuEl = $("menu");
const menuOpen = () => !menuEl.hidden;
function openMenu() {
  hideCtx();
  if (state.talking) stopTalking();
  menuEl.hidden = false;
}
function closeMenu() { menuEl.hidden = true; }
$("menu-open").addEventListener("click", () => (menuOpen() ? closeMenu() : openMenu()));
$("menu-close").addEventListener("click", closeMenu);
$("m-view").addEventListener("click", () => setView(state.view === "iso" ? "ascii" : "iso"));
$("m-tall").addEventListener("click", () => { closeMenu(); toggleTall(); });
$("m-new").addEventListener("click", () => { closeMenu(); newConversation(); });
$("m-board").addEventListener("click", () => { closeMenu(); board.open(); });
$("m-ledger").addEventListener("click", () => {
  closeMenu();
  if (!ledger.openList()) log("Writing is off in config.toml, so the ledger stays shut.");
});

function newConversation() {
  resetConversation().then(() => log("You turn to a fresh page in the ledger. Tally starts a new conversation.", "action"));
}

// ---------- input ----------

const KEYS = {
  ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
  w: [0, -1], s: [0, 1], a: [-1, 0], d: [1, 0],
  k: [0, -1], j: [0, 1], h: [-1, 0], l: [1, 0],
  y: [-1, -1], u: [1, -1], b: [-1, 1], n_: [1, 1],
  Home: [-1, -1], PageUp: [1, -1], End: [-1, 1], PageDown: [1, 1],
};

window.addEventListener("keydown", (e) => {
  touched();
  if (!ctxEl.hidden) {
    if (e.key === "Escape") { e.preventDefault(); hideCtx(); }
    return; // arrows and Enter work the menu's buttons
  }
  if (menuOpen()) {
    if (e.key === "Escape" || e.key === "m") { e.preventDefault(); closeMenu(); }
    return;
  }
  if (ledger.isOpen()) {
    if (e.key === "Escape") { e.preventDefault(); ledger.close(); }
    return;
  }
  if (board.isOpen()) {
    if (e.key === "Escape" || (e.key === "b" && !state.talking)) { e.preventDefault(); board.close(); }
    return;
  }
  if (state.talking) {
    if (e.key === "Escape") { e.preventDefault(); stopTalking(); }
    return; // typing goes to the input
  }
  if (e.key === "t" || e.key === "Enter") { e.preventDefault(); return startTalking(); }
  if (e.key === "Escape" || e.key === "m") { e.preventDefault(); return openMenu(); }
  if (e.key === "n") return newConversation();
  if (e.key === "p") { if (!ledger.openList()) log("Writing is off in config.toml, so the ledger stays shut."); return; }
  if (e.key === "e") return toggleTall();
  if (e.key === "x") return restKey("sit");
  if (e.key === "z") return restKey("lie");
  if (e.key === "b") return board.open();
  if (e.key === "v") return setView(state.view === "iso" ? "ascii" : "iso");
  const d = KEYS[e.key];
  if (d) {
    e.preventDefault();
    state.path = [];
    state.onArrive = null;
    move(...d);
  }
});

function toggleTall() {
  $("bottom").classList.toggle("tall");
  logEl.scrollTop = logEl.scrollHeight;
  if (state.talking) input.focus();
}
$("enlarge").addEventListener("click", toggleTall);

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  say(text);
});

// ---------- boot ----------

let setView = () => {};

async function boot() {
  const res = await fetch("/api/world");
  state.world = await res.json();
  const w = state.world;
  [state.player.x, state.player.y] = w.spawn;
  [state.tally.x, state.tally.y] = w.tally;
  labelShelves();

  state.player.rx = state.player.x; state.player.ry = state.player.y;
  state.tally.rx = state.tally.x; state.tally.ry = state.tally.y;

  const canvas = $("map");
  const renderers = { iso: new IsoRenderer(canvas, w), ascii: new GlyphRenderer(canvas, w) };
  const fit = () => renderers[state.view].resize($("view").getBoundingClientRect());
  setView = (v) => {
    state.view = v;
    try { localStorage.setItem("vault-view", v); } catch (_) { /* private mode */ }
    fit();
    $("view-name").textContent = v === "iso" ? "isometric" : "ASCII";
  };
  let saved = null;
  try { saved = localStorage.getItem("vault-view"); } catch (_) { /* private mode */ }
  setView(saved === "ascii" ? "ascii" : "iso");
  window.addEventListener("resize", fit);
  // The talk box grows and shrinks (talking, enlarged): keep the map sized to what is left.
  bottom.addEventListener("transitionend", (e) => { if (e.propertyName === "flex-basis") fit(); });

  const cellAt = (ev) => {
    const r = canvas.getBoundingClientRect(), rd = renderers[state.view];
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    return rd.pickFigure?.(mx, my, [state.tally]) || rd.pick(mx, my);
  };
  canvas.addEventListener("mousemove", (ev) => {
    const c = cellAt(ev);
    renderers[state.view].hover = c;
    const isTally = c && c.x === state.tally.x && c.y === state.tally.y;
    $("hover").textContent = isTally ? "Tally" : (c && describe(c.x, c.y)) || "";
  });
  canvas.addEventListener("mouseleave", () => { renderers[state.view].hover = null; $("hover").textContent = ""; });
  let swallow = false; // the click that closes the right-click menu does not also walk
  document.addEventListener("pointerdown", (ev) => {
    if (!ctxEl.hidden && !ctxEl.contains(ev.target)) { hideCtx(); swallow = ev.target === canvas && ev.button === 0; }
  }, true);
  canvas.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    swallow = false;
    const c = cellAt(ev);
    if (c) showCtx(ev.clientX, ev.clientY, c.x, c.y);
  });
  canvas.addEventListener("wheel", hideCtx, { passive: true });
  canvas.addEventListener("click", (ev) => {
    if (swallow) { swallow = false; return; }
    const c = cellAt(ev);
    if (c) walkTo(c.x, c.y);
  });
  canvas.addEventListener("wheel", (ev) => {
    const r = renderers.iso;
    if (state.view !== "iso") return;
    ev.preventDefault();
    r.zoom = Math.min(2, Math.max(0.5, r.zoom * (ev.deltaY < 0 ? 1.1 : 1 / 1.1)));
  }, { passive: false });
  portraits = {
    tally: new Portrait($("portrait-tally"), w.creatures.tally),
    player: new Portrait($("portrait-player"), w.creatures.player),
  };
  setInterval(stepAlongPath, 120);
  state.tally.post = (w.tally_posts || [])[0];
  state.tally.waitUntil = performance.now() + 30000; // at the desk when you arrive
  setDoing("round", "the game opened with you at your desk; you have not chosen anywhere yet");
  const back = restoreTally(w.tally_saved);
  state.restored = true;
  setInterval(saveTally, 5000);
  window.addEventListener("pagehide", () => saveTally(true));
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveTally(true); });
  setInterval(tallyTick, TALLY_STEP_MS);
  state.bootedAt = performance.now();
  canvas.addEventListener("pointerdown", touched);
  const tickMin = Number(w.status.tick_minutes) || 0;
  if (tickMin > 0) {
    setInterval(tallyChooses, tickMin * 60000);
    setTimeout(tallyChooses, 60000); // a first choice of their own soon after you arrive
  }
  window.vault = { state, renderers, portraits, tallyChooses, applyIntent, ringBell, reloadWorld }; // handy from the browser console

  const st = w.status;
  const statusEl = $("status");
  if (st.core_missing.length) {
    statusEl.innerHTML = `<span class="warn">Tally's core not loaded (${st.core_missing.join(", ")} missing)</span>`;
    log("Warning: Tally's core files were not found. Set tally.core_dir in config.toml. Tally will say so if you talk.", "err");
  } else {
    $("menu-status").textContent = `brain: ${st.brain} · core: ${st.core_loaded.join(", ")}`;
  }

  state.writes = st.writes || "off";
  ledger.initLedger(state.writes, log);
  board.initBoard(log);
  $("menu-status").append(` · writing: ${state.writes}` + (Number(st.tick_minutes) > 0 ? ` · Tally chooses every ${st.tick_minutes} min` : ""));

  log("You push open the heavy glass door and step into the vault.");
  const tallyRoom = roomAt(state.tally.x, state.tally.y);
  if (!back || (state.tally.x === w.tally[0] && state.tally.y === w.tally[1])) log("Somewhere north, a pen scratches in a ledger.", "action");
  else log(`Tally is where you left them: ${tallyNow()}${tallyRoom && !tallyNow().includes(tallyRoom.name) ? `, in ${tallyRoom.name}` : ""}.`, "action");
  const startRoom = roomAt(state.player.x, state.player.y);
  if (startRoom) {
    (state.announced ||= {})[startRoom.name] = performance.now();
    log(startRoom.name, "room-name");
    if (startRoom.desc) log(startRoom.desc);
  }
  log("Right-click anything to look closer. Esc opens the menu.", "sys");

  const frame = (ms) => {
    const t = ms / 1000;
    for (const e of [state.player, state.tally]) {
      e.rx += (e.x - e.rx) * 0.35;
      e.ry += (e.y - e.ry) * 0.35;
      e.moving = Math.abs(e.x - e.rx) + Math.abs(e.y - e.ry) > 0.05;
      // a step clears a pose; stopping on furniture (or where a pose was asked for) takes one
      if (e.pose && e.poseAt !== `${e.x},${e.y}`) e.pose = null;
      const still = !e.moving && (e === state.player ? !state.path.length : !state.tally.path?.length && state.tally.doing?.kind !== "follow");
      if (still && !e.pose) {
        const here = `${e.x},${e.y}`;
        if (e.wantPose && canPose(e.x, e.y, e.wantPose)) { setPose(e, e.wantPose); e.wantPose = null; }
        else if (e.declined !== here) {
          const use = tileAt(e.x, e.y)?.def?.use;
          if (use && e.lastAuto !== here) { e.lastAuto = here; setPose(e, use.poses[0]); }
        }
        if (e.wantPose && !e.path?.length) e.wantPose = null;
      }
      if (e.moving) { e.declined = null; e.lastAuto = null; }
    }
    requestAnimationFrame(frame); // first, so one bad frame cannot stop the loop
    try {
      renderers[state.view].draw(w, [state.tally, state.player], t, state.player);
      if (state.talking) { portraits.tally.draw(t); portraits.player.draw(t); }
    } catch (e) {
      if (!state.drawError) {
        state.drawError = true;
        console.error(e);
        log(`Drawing failed: ${e.message}. Press v for the ASCII view, and send this line over.`, "err");
      }
    }
  };
  requestAnimationFrame(frame);
}

boot().catch((e) => {
  log(`The vault would not open: ${e.message}`, "err");
  window.vaultFatal?.(e.message);
});
