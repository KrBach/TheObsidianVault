// The ledger: changes Tally wants to make to the vault, waiting for the owner. A '!' in the top bar
// shows when something waits; it opens the list over the map. Nothing is written until the owner approves.
import { fill } from "./names.js";

const $ = (id) => document.getElementById(id);
let items = [];
let open = null;
let onLog = () => {};
let enabled = false;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function kindLabel(op) {
  if (op.kind === "errand") return "errand";
  if (op.kind === "append") return op.section?.includes("coaching") ? "coaching line" : "log line";
  return op.kind === "create" ? "new note" : "note edit";
}

export const pendingCount = () => items.length;

function renderList(mode, size) {
  const list = $("ledger-list");
  list.replaceChildren();
  $("ledger-count").textContent = items.length > 1 ? String(items.length) : "";
  $("ledger-flag").hidden = !items.length;
  if (!items.length) {
    list.append(el("div", "dim", mode === "direct"
      ? "Nothing waiting. Log lines are written at once; note changes wait here."
      : "Nothing waiting for you."));
  }
  for (const op of items) {
    const row = el("button", "ledger-item" + (op.stale ? " stale" : ""));
    row.type = "button";
    row.append(el("span", "kind", kindLabel(op)), el("span", "file", op.file), el("span", "sum", op.summary || ""));
    row.addEventListener("click", () => show(op.id));
    list.append(row);
  }
  $("ledger-size").textContent = size || "";
}

function renderDiff(pre, op) {
  pre.replaceChildren();
  if (op.stale) { pre.append(el("span", "del", op.diff)); return; }
  if (op.kind === "errand") { pre.append(el("span", "", op.diff)); return; }
  for (const line of op.diff.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++")) continue;
    const cls = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
    pre.append(el("span", cls, line + "\n"));
  }
}

function show(id) {
  const op = items.find((o) => o.id === id);
  if (!op) return close();
  open = id;
  $("ledger-pop").hidden = true;
  const box = $("review");
  box.hidden = false;
  $("review-title").textContent = `${kindLabel(op)} · ${op.file}`;
  $("review-why").textContent = op.summary || "";
  renderDiff($("review-diff"), op);
  $("review-approve").disabled = !!op.stale;
  $("review-approve").textContent = op.kind === "errand" ? "Approve and send" : "Approve and write";
  $("review-msg").textContent = op.stale ? fill("This change no longer applies. Reject it and ask Tally again.") : "";
}

export function close() {
  const was = open;
  open = null;
  $("review").hidden = true;
  // back to the list if more pages wait; otherwise close everything
  if (was && items.some((o) => o.id !== was)) openList();
  else $("ledger-pop").hidden = true;
}

export function openList() {
  if (!enabled) return false;
  open = null;
  $("review").hidden = true;
  $("ledger-pop").hidden = false;
  refresh();
  return true;
}

export function isOpen() { return open !== null || !$("ledger-pop").hidden; }

async function decide(action) {
  if (!open) return;
  const id = open;
  $("review-approve").disabled = $("review-reject").disabled = true;
  try {
    const r = await fetch(`/api/pending/${id}/${action}`, { method: "POST" });
    const data = await r.json();
    if (data.ok) {
      onLog(action === "approve" ? `You sign the ledger. ${data.message}` : "You strike the entry out. Nothing was written.", "action");
      if (action === "approve") window.dispatchEvent(new CustomEvent("vault:errands"));
      open = null;
      $("review").hidden = true;
      await refresh();
      if (items.length) openList();
      return;
    } else {
      $("review-msg").textContent = data.error;
    }
  } finally {
    $("review-reject").disabled = false;
    await refresh();
  }
}

export async function refresh() {
  try {
    const r = await fetch("/api/pending");
    const data = await r.json();
    items = data.pending;
    renderList(data.mode, data.size);
    if (open) show(open);
    return items.length;
  } catch {
    return 0;
  }
}

export function initLedger(mode, log) {
  onLog = log;
  if (mode === "off") return;
  enabled = true;
  $("ledger-flag").addEventListener("click", openList);
  $("ledger-close").addEventListener("click", () => { $("ledger-pop").hidden = true; });
  $("review-approve").addEventListener("click", () => decide("approve"));
  $("review-reject").addEventListener("click", () => decide("reject"));
  $("review-close").addEventListener("click", close);
  refresh();
}
