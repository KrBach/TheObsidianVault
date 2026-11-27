// Talks to the local server, which streams Tally's reply as server-sent events.

export async function talk(text, where, handlers, extra = {}) {
  const res = await fetch("/api/talk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...extra, text, where }),
  });
  if (!res.ok || !res.body) throw new Error(`server said ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (!chunk.startsWith("data: ")) continue;
      const ev = JSON.parse(chunk.slice(6));
      if (ev.type === "done") return;
      handlers[ev.type]?.("text" in ev ? ev.text : ev);
    }
  }
}

// A new conversation: for OWNER, Tally first gets a last turn in the old one (a log line and a handoff).
export async function resetConversation() {
  const r = await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  return r.json().catch(() => ({}));
}
