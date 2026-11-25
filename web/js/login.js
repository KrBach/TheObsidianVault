// The door: who comes in? the owner, with a password, or a guest.
// The first time, the owner chooses the password here; only a hash of it is kept, on this machine.

import { fill } from "./names.js";

const $ = (id) => document.getElementById(id);

async function post(body) {
  const r = await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.ok) throw new Error(data.error || `the door would not open (${r.status})`);
  return data.role;
}

// Resolves with the role once someone is in.
export function login(hasPassword, portrait = "") {
  const box = $("login"), form = $("login-form"), err = $("login-error");
  if (portrait) {
    const img = $("login-owner-img");
    img.onload = () => { img.hidden = false; $("login-owner-art").hidden = true; };
    img.src = portrait;
  }
  const pw = $("login-pw"), pw2 = $("login-pw2");
  box.hidden = false;
  return new Promise((resolve) => {
    const done = (role) => { box.hidden = true; resolve(role); };
    const fail = (e) => { err.textContent = e.message; };

    $("login-owner").addEventListener("click", () => {
      err.textContent = "";
      form.hidden = false;
      $("login-owner").classList.add("chosen");
      $("login-guest").classList.remove("chosen");
      $("login-hint").textContent = hasPassword ? "Password" : fill("First time here: choose a password for OWNER");
      pw2.hidden = hasPassword;
      pw.focus();
    });
    $("login-guest").addEventListener("click", () => {
      err.textContent = "";
      post({ role: "guest" }).then(done, fail);
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      err.textContent = "";
      if (!hasPassword && pw.value !== pw2.value) return fail(new Error("the two passwords differ"));
      post({ role: "owner", password: pw.value, new_password: !hasPassword }).then(done, (e2) => {
        pw.value = ""; pw2.value = ""; pw.focus(); fail(e2);
      });
    });
  });
}

export async function logout() {
  await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  location.reload();
}
