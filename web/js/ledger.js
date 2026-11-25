// The ledger panel: changes Tally wants to make to the vault, waiting for Kristian.
// Nothing is written until he approves a change here.

const $ = (id) => document.getElementById(id);
let items = [];
let open = null;
let onLog = () => {};

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

function renderList(mode, size) {
  const list = $("ledger-list");
  list.replaceChildren();
  $("ledger-count").textContent = items.length ? `${items.length} waiting` : "";
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
  const box = $("review");
  box.hidden = false;
  $("review-title").textContent = `${kindLabel(op)} · ${op.file}`;
  $("review-why").textContent = op.summary || "";
  renderDiff($("review-diff"), op);
  $("review-approve").disabled = !!op.stale;
  $("review-approve").textContent = op.kind === "errand" ? "Approve and send" : "Approve and write";
  $("review-msg").textContent = op.stale ? "This change no longer applies. Reject it and ask Tally again." : "";
}

export function close() {
  open = null;
  $("review").hidden = true;
}

export function isOpen() { return open !== null; }

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
      close();
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
  $("ledger").hidden = false;
  $("review-approve").addEventListener("click", () => decide("approve"));
  $("review-reject").addEventListener("click", () => decide("reject"));
  $("review-close").addEventListener("click", close);
  refresh();
}
