// The Character window (owner only): your name, your companion's name, your portrait, the time
// zone for log timestamps, and your password. Saved by the server in .vault-state/settings.json.

const $ = (id) => document.getElementById(id);
let portrait = null; // a data URL waiting to be saved, "" to remove, null to leave as it is

function showImage(src) {
  const img = $("char-img");
  if (src) { img.src = src; img.hidden = false; $("char-art").hidden = true; }
  else { img.hidden = true; img.removeAttribute("src"); $("char-art").hidden = false; }
}

export async function open() {
  $("character").hidden = false;
  $("char-msg").textContent = "";
  portrait = null;
  for (const id of ["char-old", "char-new", "char-new2"]) $(id).value = "";
  try {
    const c = await (await fetch("/api/character")).json();
    $("char-owner").value = c.owner || "";
    $("char-companion").value = c.companion || "";
    $("char-tz").value = c.timezone || "";
    showImage(c.owner_portrait ? `${c.owner_portrait}?t=${Date.now()}` : "");
  } catch (e) {
    $("char-msg").textContent = `Could not load the settings: ${e.message}`;
  }
  $("char-owner").focus();
}

export function close() { $("character").hidden = true; }
export function isOpen() { return !$("character").hidden; }

async function save(e) {
  e.preventDefault();
  const msg = $("char-msg");
  msg.textContent = "";
  const body = { owner: $("char-owner").value, companion: $("char-companion").value, timezone: $("char-tz").value.trim() };
  if (portrait) body.portrait = portrait;
  if (portrait === "") { body.portrait = ""; body.remove_portrait = true; }
  if ($("char-new").value || $("char-old").value) {
    if ($("char-new").value !== $("char-new2").value) { msg.textContent = "The two new passwords differ."; return; }
    body.old_password = $("char-old").value;
    body.new_password = $("char-new").value;
  }
  try {
    const r = await fetch("/api/character", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) { msg.textContent = data.error || `Could not save (${r.status}).`; return; }
    location.reload(); // every name on the page, and in what the companion is told, changes at once
  } catch (err) {
    msg.textContent = `Could not save: ${err.message}`;
  }
}

export function initCharacter() {
  $("character-close").addEventListener("click", close);
  $("character-form").addEventListener("submit", save);
  $("char-remove").addEventListener("click", () => { portrait = ""; showImage(""); });
  $("char-file").addEventListener("change", () => {
    const f = $("char-file").files[0];
    if (!f) return;
    if (f.size > 3 * 1024 * 1024) { $("char-msg").textContent = "That image is over 3 MB; pick a smaller one."; return; }
    const reader = new FileReader();
    reader.onload = () => { portrait = reader.result; showImage(portrait); };
    reader.readAsDataURL(f);
  });
  try {
    const list = $("char-tz-list");
    for (const tz of Intl.supportedValuesOf("timeZone")) list.append(Object.assign(document.createElement("option"), { value: tz }));
  } catch (_) { /* an older browser: type the name */ }
}
