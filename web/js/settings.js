// The Settings window (owner only): the vault folders, the errands browser and the idle time after
// which the conversation with Tally closes (with a handoff; the game keeps running). Saved by the server
// at once in the local config file, outside the game's folder, so an update never loses them.
// The server refuses this to a guest, and the menu hides it for one.

const $ = (id) => document.getElementById(id);

function mark(id, f) {
  const el = $(id);
  el.textContent = !f.path ? "not set" : f.exists ? "✓ found" : "✗ not found on this computer";
  el.className = `small ${f.path && !f.exists ? "warn" : "dim"}`;
}

function fill(s) {
  $("set-core").value = s.core_dir.path || "";
  $("set-work").value = s.work_dir.path || "";
  mark("set-core-ok", s.core_dir);
  mark("set-work-ok", s.work_dir);
  const sel = $("set-browser");
  sel.textContent = "";
  for (const b of s.browsers) sel.append(Object.assign(document.createElement("option"), { value: b, textContent: b }));
  sel.value = s.browser;
  $("set-idle").value = s.idle_minutes ?? 30;
  $("set-file").textContent = s.config_file ? `Saved in ${s.config_file}, outside the game's folder.` : "";
}

export async function open() {
  $("settings").hidden = false;
  $("set-msg").textContent = "";
  try {
    const r = await fetch("/api/settings");
    if (!r.ok) throw new Error(r.status === 403 ? "these are the owner's" : `status ${r.status}`);
    fill(await r.json());
  } catch (e) {
    $("set-msg").textContent = `Could not load the settings: ${e.message}`;
  }
  $("set-core").focus();
}

export function close() { $("settings").hidden = true; }
export function isOpen() { return !$("settings").hidden; }

async function save(e) {
  e.preventDefault();
  const msg = $("set-msg");
  msg.textContent = "";
  const body = { core_dir: $("set-core").value.trim(), work_dir: $("set-work").value.trim(), browser: $("set-browser").value,
    idle_minutes: $("set-idle").value.trim() === "" ? 30 : Number($("set-idle").value) };
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) { msg.textContent = data.error || `Could not save (${r.status}).`; return; }
    fill(data);
    const said = data.changed.length ? "Saved." : "Nothing changed.";
    const fresh = data.changed.includes("core_dir") ? " Tally starts a new conversation in the new vault." : "";
    msg.textContent = [said + fresh, ...(data.notes || [])].join(" ");
    msg.className = data.notes && data.notes.length ? "warn" : "dim";
    if (data.changed.includes("core_dir")) setTimeout(() => location.reload(), 1500); // new shelves
  } catch (err) {
    msg.textContent = `Could not save: ${err.message}`;
  }
}

export function initSettings(openCharacter) {
  $("settings-close").addEventListener("click", close);
  $("settings-form").addEventListener("submit", save);
  $("set-character").addEventListener("click", () => { close(); openCharacter(); });
}
