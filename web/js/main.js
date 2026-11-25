import { GlyphRenderer } from "./renderer.js";
import { IsoRenderer } from "./iso.js";
import { Portrait } from "./portrait.js";
import { talk, resetConversation } from "./talk.js";
import { renderMarkdown } from "./markdown.js";
import * as ledger from "./ledger.js";

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
  return t.def.desc ? `${t.def.name}. ${t.def.desc}` : t.def.name;
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

function updateSide() {
  const { x, y } = state.player;
  const room = roomAt(x, y);
  $("room-name").textContent = room ? room.name : "Somewhere in the vault";
  $("room-desc").textContent = room ? room.desc : "";

  const near = new Set();
  if (inTalkRange(state.player, state.tally)) near.add("Tally is right here. Press t to talk.");
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      if (!dx && !dy) continue;
      const t = tileAt(x + dx, y + dy);
      if (t?.def && !["floor", "floor_glint", "wall"].includes(t.id)) {
        const d = t.id.includes("shelf") ? describe(x + dx, y + dy) : t.def.name;
        if (d) near.add(d);
      }
    }
  }
  const nearby = $("nearby");
  nearby.replaceChildren(...[...near].map((s) => Object.assign(document.createElement("div"), { textContent: s })));
  if (!near.size) nearby.innerHTML = '<span class="dim">nothing of note</span>';
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
//   round   walking between the posts in world.tally_posts on their own (the default)
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
    setDoing("round", "back on the usual round");
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
  if (held || !posts.length || now < (t.waitUntil || 0)) return;
  const choices = posts.filter((p) => p !== t.post);
  // the desk is home: come back to it more often than anywhere else
  const goal = Math.random() < 0.4 && t.post !== posts[0] ? posts[0] : choices[Math.floor(Math.random() * choices.length)];
  const path = bfs([t.x, t.y], [goal.at], tallyFree);
  if (path === null) { t.waitUntil = now + 5000; return; }
  t.goal = goal;
  t.path = path;
  t.doing.why = `on the usual round, ${roomOf(goal) ? `looking in on ${roomOf(goal).name}` : "looking about"}`;
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
function sendTally(at, name, room, why, firmMs = 0) {
  const t = state.tally;
  const path = bfs([t.x, t.y], [at], tallyFree);
  if (path === null) { log(`Tally can't find a way ${name}.`, "action"); return false; }
  setDoing("go", why, { name, firmUntil: firmMs ? performance.now() + firmMs : 0 });
  t.goal = { at, room, name, dwell: [60, 120] };
  t.path = path;
  t.arrivedAt = path.length ? 0 : performance.now();
  return true;
}

// What Tally said they would do, from the game tools in a reply: go, stay, follow or wander.
function applyIntent(ev) {
  const t = state.tally;
  const firm = (Number(ev.minutes) || 0) * 60000;
  if (ev.kind === "go" && ev.at) {
    if (sendTally(ev.at, ev.name, ev.room, ev.why, firm) && t.path.length) log(`Tally sets off, ${ev.name}.`, "action");
  } else if (ev.kind === "stay") {
    t.path = null;
    setDoing("stay", ev.why, { firmUntil: firm ? performance.now() + firm : 0 });
  } else if (ev.kind === "follow") {
    t.path = null;
    setDoing("follow", ev.why, { firmUntil: firm ? performance.now() + firm : 0 });
    log("Tally falls in beside you.", "action");
  } else if (ev.kind === "free") {
    setDoing("round", "free to wander");
    t.waitUntil = performance.now() + 20000;
  }
}

// Where Tally is, what they are doing and why: for the talk's game_state line and for ticks.
function doingText() {
  const t = state.tally, d = t.doing || { kind: "round" };
  if (d.kind === "follow") return "walking with Kristian";
  if (d.kind === "go") return t.path?.length ? `on your way ${d.name}` : `staying ${d.name}`;
  if (d.kind === "stay") return "staying here, as you chose";
  return t.path?.length ? "walking on your usual round" : "pausing on your usual round";
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
  if (res.go === "stay" || !res.at) {
    // keep doing what Tally was doing, renewed until the next tick
    const d = t.doing;
    if (!d || d.kind === "round") setDoing("stay", res.why || "chose to stay a while");
    else { d.softUntil = performance.now() + SOFT_HOLD_MS; if (res.why) d.why = res.why; }
    return;
  }
  if (res.at[0] === t.x && res.at[1] === t.y) { setDoing("go", res.why, { name: res.name }); return; }
  if (sendTally(res.at, res.name, res.room, res.why || "") && !res.say) log(`Tally sets off, ${res.name}.`, "action");
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
    if (!seen[after.name] || now - seen[after.name] > 30000) log(`You enter ${after.name}.`);
    seen[after.name] = now;
  }
  if (nx === state.world.exit[0] && ny === state.world.exit[1]) log("The way out. Close the tab to leave; Tally keeps the ledger.");
  if (t.id === "papers" || t.id === "mat") log(describe(nx, ny));
  updateSide();
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
  const where = `Kristian is standing in ${room ? room.name : "the vault"}, next to you. ` +
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
  if (ledger.isOpen()) {
    if (e.key === "Escape") { e.preventDefault(); ledger.close(); }
    return;
  }
  if (state.talking) {
    if (e.key === "Escape") { e.preventDefault(); stopTalking(); }
    return; // typing goes to the input
  }
  if (e.key === "t" || e.key === "Enter") { e.preventDefault(); return startTalking(); }
  if (e.key === "n") {
    resetConversation().then(() => log("You turn to a fresh page in the ledger. Tally starts a new conversation.", "action"));
    return;
  }
  if (e.key === "e") return toggleTall();
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
    const r = canvas.getBoundingClientRect();
    return renderers[state.view].pick(ev.clientX - r.left, ev.clientY - r.top);
  };
  canvas.addEventListener("mousemove", (ev) => {
    const c = cellAt(ev);
    renderers[state.view].hover = c;
    const isTally = c && c.x === state.tally.x && c.y === state.tally.y;
    $("hover").textContent = isTally ? "Tally" : (c && describe(c.x, c.y)) || "";
  });
  canvas.addEventListener("mouseleave", () => { renderers[state.view].hover = null; $("hover").textContent = ""; });
  canvas.addEventListener("click", (ev) => {
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
  setDoing("round", "at the desk, keeping the ledger");
  setInterval(tallyTick, TALLY_STEP_MS);
  state.bootedAt = performance.now();
  canvas.addEventListener("pointerdown", touched);
  const tickMin = Number(w.status.tick_minutes) || 0;
  if (tickMin > 0) setInterval(tallyChooses, tickMin * 60000);
  window.vault = { state, renderers, portraits, tallyChooses, applyIntent, ringBell, reloadWorld }; // handy from the browser console

  const st = w.status;
  const statusEl = $("status");
  if (st.core_missing.length) {
    statusEl.innerHTML = `<span class="warn">Tally's core not loaded (${st.core_missing.join(", ")} missing)</span>`;
    log("Warning: Tally's core files were not found. Set tally.core_dir in config.toml. Tally will say so if you talk.", "err");
  } else {
    statusEl.textContent = `brain: ${st.brain} · core: ${st.core_loaded.join(", ")}`;
  }

  state.writes = st.writes || "off";
  ledger.initLedger(state.writes, log);
  if (state.writes !== "off") statusEl.append(` · writing: ${state.writes}`);

  log("You push open the heavy glass door and step into the vault.");
  log("Somewhere north, a pen scratches in a ledger.", "action");
  updateSide();

  const frame = (ms) => {
    const t = ms / 1000;
    for (const e of [state.player, state.tally]) {
      e.rx += (e.x - e.rx) * 0.35;
      e.ry += (e.y - e.ry) * 0.35;
      e.moving = Math.abs(e.x - e.rx) + Math.abs(e.y - e.ry) > 0.05;
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
