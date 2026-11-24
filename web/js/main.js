import { Renderer } from "./renderer.js";
import { talk, resetConversation } from "./talk.js";

const $ = (id) => document.getElementById(id);
const logEl = $("log"), form = $("talk"), input = $("say");

const SHELF_COLOURS = ["#c98a5a", "#7fb07a", "#c0a050", "#6a9ad0", "#b07ac0", "#d07070", "#70c0c0", "#a0a0a0"];

const state = {
  world: null,
  player: { kind: "player", x: 0, y: 0 },
  tally: { kind: "tally", x: 0, y: 0, alpha: 1 },
  talking: false,
  busy: false,
  shelfAt: new Map(), // "x,y" -> shelf info
};

// ---------- log ----------

function log(text, cls = "sys") {
  const div = document.createElement("div");
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
  let n = 0;
  for (let y = 0; y < map.length; y++) {
    for (let x = 0; x < map[y].length; x++) {
      const ch = map[y][x];
      if (ch !== "S" && ch !== "A") continue;
      if (seen.has(`${x},${y}`)) continue;
      // flood along the row: shelves are horizontal runs
      let info;
      if (ch === "S") {
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

function isAdjacent(a, b) {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)) === 1;
}

function updateSide() {
  const { x, y } = state.player;
  const room = roomAt(x, y);
  $("room-name").textContent = room ? room.name : "Somewhere in the vault";
  $("room-desc").textContent = room ? room.desc : "";

  const near = new Set();
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      if (!dx && !dy) continue;
      if (state.tally.x === x + dx && state.tally.y === y + dy) near.add("Tally is right here. Press t to talk.");
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
  if (after && after !== before) log(`You enter ${after.name}.`);
  if (nx === state.world.exit[0] && ny === state.world.exit[1]) log("The way out. Close the tab to leave; Tally keeps the ledger.");
  if (t.id === "papers" || t.id === "mat") log(describe(nx, ny));
  updateSide();
}

function startTalking() {
  if (!isAdjacent(state.player, state.tally)) {
    log("Tally is at the desk in the Hearth. Walk up to the desk first.");
    return;
  }
  if (!state.talking) log("Tally looks up from the ledger.", "action");
  state.talking = true;
  form.hidden = false;
  input.focus();
}

function stopTalking() {
  if (!state.talking) return;
  state.talking = false;
  form.hidden = true;
  input.blur();
  log("You step back from the desk.", "action");
}

async function say(text) {
  if (state.busy) return;
  state.busy = true;
  input.disabled = true;
  logLine("Kristian", "you").textContent = text;
  let body = logLine("Tally", "tally");
  body.classList.add("thinking");
  // Tool use shows as an action line. Keep the order true: action first if Tally has not
  // spoken yet, otherwise start a fresh Tally line after it.
  const onAction = (t) => {
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
  const where = `Kristian is standing in ${room ? room.name : "the vault"}, next to your desk.`;
  try {
    await talk(text, where, {
      delta: (t) => { body.textContent += t; logEl.scrollTop = logEl.scrollHeight; },
      action: onAction,
      error: (t) => log(t, "err"),
    });
  } catch (e) {
    log(`Lost the connection to the vault: ${e.message}`, "err");
  }
  body.classList.remove("thinking");
  if (!body.textContent) body.parentElement.remove();
  state.busy = false;
  input.disabled = false;
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
  if (state.talking) {
    if (e.key === "Escape") { e.preventDefault(); stopTalking(); }
    return; // typing goes to the input
  }
  if (e.key === "t" || e.key === "Enter") { e.preventDefault(); return startTalking(); }
  if (e.key === "n") {
    resetConversation().then(() => log("You turn to a fresh page in the ledger. Tally starts a new conversation.", "action"));
    return;
  }
  const d = KEYS[e.key];
  if (d) { e.preventDefault(); move(...d); }
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  say(text);
});

// ---------- boot ----------

async function boot() {
  const res = await fetch("/api/world");
  state.world = await res.json();
  const w = state.world;
  [state.player.x, state.player.y] = w.spawn;
  [state.tally.x, state.tally.y] = w.tally;
  labelShelves();

  const renderer = new Renderer($("map"), w);
  const fit = () => renderer.resize(w.map[0].length, w.map.length, $("view").getBoundingClientRect());
  window.addEventListener("resize", fit);
  fit();

  const st = w.status;
  const statusEl = $("status");
  if (st.core_missing.length) {
    statusEl.innerHTML = `<span class="warn">Tally's core not loaded (${st.core_missing.join(", ")} missing)</span>`;
    log("Warning: Tally's core files were not found. Set tally.core_dir in config.toml. Tally will say so if you talk.", "err");
  } else {
    statusEl.textContent = `brain: ${st.brain} · core: ${st.core_loaded.join(", ")}`;
  }

  log("You push open the heavy glass door and step into the vault.");
  log("Somewhere north, a pen scratches in a ledger.", "action");
  updateSide();

  const frame = (ms) => {
    const t = ms / 1000;
    state.tally.alpha = state.busy ? 0.55 + 0.45 * Math.sin(t * 6) : 1;
    renderer.draw(w, [state.tally, state.player], t);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

boot().catch((e) => log(`The vault would not open: ${e.message}`, "err"));
