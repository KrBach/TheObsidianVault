// Who is who, from config.toml [names] via the server. The game's text says OWNER where the
// owner's name goes and Tally for the companion; fill() puts the configured names in.

export const names = { owner: "Owner", companion: "Tally", owner_portrait: "" };

export function setNames(n) {
  if (n && typeof n === "object") Object.assign(names, n);
}

export function fill(text) {
  let t = String(text ?? "").replace(/\bOWNER\b/g, names.owner);
  if (names.companion !== "Tally") t = t.replace(/\bTally\b/g, names.companion);
  return t;
}

// The page's own text (menu, controls, login): fill every text node and a few attributes once.
export function fillPage(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = fill(n.nodeValue);
    if (t !== n.nodeValue) n.nodeValue = t;
  }
  for (const el of root.querySelectorAll("[title], [placeholder]")) {
    for (const a of ["title", "placeholder"]) if (el.hasAttribute(a)) el.setAttribute(a, fill(el.getAttribute(a)));
  }
  document.title = fill(document.title);
}
