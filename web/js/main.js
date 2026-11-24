import { GlyphRenderer } from "./renderer.js";
import { IsoRenderer } from "./iso.js";
import { Portrait } from "./portrait.js";
import { talk, resetConversation } from "./talk.js";
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
    if (t.id !== "wall") onArrive = () => log(describe(x, y));
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

// ---------- Tally wanders ----------
// Tally walks between posts (world.tally_posts): the desk, the Library, the Scriptorium, the
// Map Room. Tally lingers at each, and stays put while Kristian is close, walking up, or talking.

const TALLY_STEP_MS = 380;
const NOTICE_RANGE = 3;

function tallyFree(x, y) {
  const t = tileAt(x, y);
  return !!(t && t.def && !t.def.solid) && !(x === state.player.x && y === state.player.y);
}

function tallyShouldStay() {
  const p = state.player, t = state.tally;
  const close = Math.max(Math.abs(p.x - t.x), Math.abs(p.y - t.y)) <= NOTICE_RANGE;
  return state.busy || state.talking || close || (state.goingToTally && !!state.onArrive);
}

function tallyTick() {
  const t = state.tally, posts = state.world.tally_posts || [];
  if (!posts.length) return;
  const now = performance.now();
  if (tallyShouldStay()) {
    // look up and wait a little after Kristian leaves, rather than rushing off
    t.waitUntil = Math.max(t.waitUntil || 0, now + 8000);
    return;
  }
  if (t.path?.length) {
    const [nx, ny] = t.path[0];
    if (!tallyFree(nx, ny)) { t.path = null; return; } // someone is in the way: re-plan next tick
    t.path.shift();
    const before = roomAt(t.x, t.y);
    t.x = nx; t.y = ny;
    const after = roomAt(nx, ny);
    const mine = roomAt(state.player.x, state.player.y);
    if (after !== before && mine && (before === mine || after === mine)) {
      log(after === mine ? `Tally walks in.` : `Tally heads off toward ${roomOf(t.goal)?.name || "another room"}.`, "action");
    }
    if (!t.path.length) {
      const [a, b] = t.goal.dwell || [20, 60];
      t.waitUntil = now + (a + Math.random() * (b - a)) * 1000;
      t.post = t.goal;
    }
    return;
  }
  if (now < (t.waitUntil || 0)) return;
  const choices = posts.filter((p) => p !== t.post);
  // the desk is home: come back to it more often than anywhere else
  const goal = Math.random() < 0.4 && t.post !== posts[0] ? posts[0] : choices[Math.floor(Math.random() * choices.length)];
  const path = bfs([t.x, t.y], [goal.at], tallyFree);
  if (path === null) { t.waitUntil = now + 5000; return; }
  t.goal = goal;
  t.path = path;
}

const roomOf = (post) => post && state.world.rooms.find((r) => r.id === post.room);

// ---------- actions ----------

function move(dx, dy) {
  const nx = state.player.x + dx, ny = state.player.y + dy;
  if (nx === state.tally.x && ny === state.tally.y) return startTalking();
  const t = tileAt(nx, ny);
  if (!t || !t.def) return;
  if (t.def.solid) {
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
    `You are ${atDesk ? "at your desk in the Hearth" : `in ${tallyRoom ? tallyRoom.name : "the vault"}`}.`;
  try {
    await talk(text, where, {
      delta: (t) => { setTallyState("speaking"); body.textContent += t; logEl.scrollTop = logEl.scrollHeight; },
      action: onAction,
      error: (t) => log(t, "err"),
    });
  } catch (e) {
    log(`Lost the connection to the vault: ${e.message}`, "err");
  }
  body.classList.remove("thinking");
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
  if (e.key === "v") return setView(state.view === "iso" ? "ascii" : "iso");
  const d = KEYS[e.key];
  if (d) {
    e.preventDefault();
    state.path = [];
    state.onArrive = null;
    move(...d);
  }
});

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
  setInterval(tallyTick, TALLY_STEP_MS);
  window.vault = { state, renderers, portraits }; // handy from the browser console

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
