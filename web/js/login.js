// The door: who comes in? Kristian, with his password, or a guest.
// The first time, Kristian chooses the password here; only a hash of it is kept, on his machine.

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
export function login(hasPassword) {
  const box = $("login"), form = $("login-form"), err = $("login-error");
  const pw = $("login-pw"), pw2 = $("login-pw2");
  box.hidden = false;
  return new Promise((resolve) => {
    const done = (role) => { box.hidden = true; resolve(role); };
    const fail = (e) => { err.textContent = e.message; };

    $("login-kristian").addEventListener("click", () => {
      err.textContent = "";
      form.hidden = false;
      $("login-kristian").classList.add("chosen");
      $("login-guest").classList.remove("chosen");
      $("login-hint").textContent = hasPassword ? "Password" : "First time here: choose a password for Kristian";
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
      post({ role: "kristian", password: pw.value, new_password: !hasPassword }).then(done, (e2) => {
        pw.value = ""; pw2.value = ""; pw.focus(); fail(e2);
      });
    });
  });
}

export async function logout() {
  await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  location.reload();
}
