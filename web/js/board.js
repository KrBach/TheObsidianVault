// The notice board: errand reports, pages from the vault's 'Claude outputs' folder, and pinned links.
// Opens over the map with `b`. Pages run in a sandboxed frame, never as the game.

import { renderMarkdown } from "./markdown.js";
import { fill } from "./names.js";

const $ = (id) => document.getElementById(id);
let items = [];
let isOpenFlag = false;
let onLog = () => {};
const known = new Map(); // errand id -> last status seen, to announce finished errands once
let polling = null;
const SEEN_KEY = "vault-board-seen";
let seen = null; // newest 'updated' time the owner has seen on the board (kept in this browser)

function newest() { return items.reduce((m, it) => Math.max(m, Number(it.updated) || 0), 0); }
function markSeen() {
  seen = newest();
  try { localStorage.setItem(SEEN_KEY, String(seen)); } catch { /* private mode */ }
}

// What the board in the Entrance Hall shows: one paper per item, and whether something is new.
export function papers() {
  return { count: items.length, fresh: seen !== null && newest() > seen };
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const LABEL = { link: "link", report: "errand", note: "note", image: "image", page: "page" };

function when(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function renderList() {
  const list = $("board-list");
  list.replaceChildren();
  if (!items.length) list.append(el("div", "dim", fill("The board is bare. Errand reports, files in 'Claude outputs' and links Tally pins appear here.")));
  for (const it of items) {
    const row = el("button", "board-item");
    row.type = "button";
    const status = it.kind === "report" && it.status !== "done" ? ` · ${it.status}` : "";
    row.append(el("span", "kind", LABEL[it.kind] || it.kind), el("span", "file", it.title),
      el("span", "sum", `${it.where || ""}${status} · ${when(it.updated)}`));
    row.addEventListener("click", () => show(it));
    list.append(row);
  }
  $("board-count").textContent = items.length ? String(items.length) : "";
}

async function show(it) {
  const view = $("board-view");
  view.replaceChildren();
  $("board-title").textContent = it.title;
  if (it.kind === "link") {
    const a = el("a", "", it.url);
    a.href = it.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    view.append(el("div", "dim", it.where || "A pinned link. It opens in a new tab."), a);
    return;
  }
  if (it.kind === "image") {
    const img = el("img", "board-img");
    img.src = `/api/board/file/${it.id}`;
    view.append(img);
    return;
  }
  if (it.kind === "page") {
    const frame = el("iframe", "board-frame");
    frame.setAttribute("sandbox", "allow-scripts allow-popups");
    frame.src = `/api/board/file/${it.id}`;
    view.append(frame);
    return;
  }
  const text = el("div", "board-text");
  view.append(text);
  try {
    if (it.kind === "report") {
      const r = await (await fetch(`/api/board/report/${it.id}`)).json();
      const head = [`**Errand (${r.kind}):** ${r.title}`, `**Status:** ${r.status}`];
      if (r.branch) head.push(`**Branch:** \`${r.branch}\`${r.commits ? ` (${r.commits.length} commit${r.commits.length === 1 ? "" : "s"})` : ""}`);
      if (r.cost_usd != null) head.push(`**Cost:** $${Number(r.cost_usd).toFixed(2)}`);
      const body = r.status === "running" ? "_Still running._" : (r.result || "(no report)");
      text.innerHTML = renderMarkdown(`${head.join("\n")}\n\n${body}\n\n# Brief\n${r.brief}`);
    } else {
      text.innerHTML = renderMarkdown(await (await fetch(`/api/board/file/${it.id}`)).text());
    }
  } catch (e) {
    text.textContent = `Could not open it: ${e.message}`;
  }
}

export async function refresh() {
  try {
    items = (await (await fetch("/api/board")).json()).items || [];
  } catch { items = []; }
  if (seen === null) {
    try { seen = Number(localStorage.getItem(SEEN_KEY)) || null; } catch { /* private mode */ }
    if (seen === null) markSeen(); // the first visit: what is already up is not news
  }
  if (isOpenFlag) markSeen();
  renderList();
}

export async function open() {
  isOpenFlag = true;
  $("board").hidden = false;
  $("board-view").replaceChildren(el("div", "dim", "Pick something on the left."));
  $("board-title").textContent = "";
  await refresh();
}

export function close() {
  isOpenFlag = false;
  $("board").hidden = true;
  $("board-view").replaceChildren();
}

export function isOpen() { return isOpenFlag; }

async function checkErrands() {
  let list = [];
  try { list = (await (await fetch("/api/errands")).json()).errands || []; } catch { return; }
  let running = false;
  for (const e of list) {
    const before = known.get(e.id);
    if (before === "running" && e.status !== "running") {
      onLog(e.status === "done"
        ? `A runner comes back: the errand "${e.title}" is done. Its report is on the notice board (b).`
        : `A runner comes back empty-handed: the errand "${e.title}" failed. See the notice board (b).`, "action");
      if (isOpenFlag) refresh();
    }
    known.set(e.id, e.status);
    if (e.status === "running") running = true;
  }
  clearTimeout(polling);
  if (running) polling = setTimeout(checkErrands, 20000);
}

export function initBoard(log) {
  onLog = log;
  $("board-close").addEventListener("click", close);
  $("board-open").addEventListener("click", open);
  window.addEventListener("vault:errands", () => setTimeout(checkErrands, 1000));
  refresh();
  setInterval(refresh, 120000); // new pages and pinned links show on the board in the hall
  checkErrands();
}
