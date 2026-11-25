// Voice in the talk box, both ways, with what the browser already has (no packages, no keys):
//   - speaking to Tally: the mic button next to the talk line uses the browser's speech recognition
//     (in Safari it goes through Apple's dictation, in Chrome through Google's servers); what you say
//     appears in the line and is sent when you stop talking;
//   - Tally speaking: switched on in the menu, each finished reply is read aloud with the system voice.
// Both are off until you use them, and the choice to hear Tally is kept in this browser.

const KEY = "vault-voice-out";
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let speakOn = false;
let rec = null;

function load() {
  try { speakOn = localStorage.getItem(KEY) === "on"; } catch { speakOn = false; }
}

export const canListen = () => !!Recognition;
export const canSpeak = () => "speechSynthesis" in window;
export const speaking = () => speakOn;

export function setSpeaking(on) {
  speakOn = !!on && canSpeak();
  try { localStorage.setItem(KEY, speakOn ? "on" : "off"); } catch { /* private mode */ }
  if (!speakOn) hush();
}

export function hush() {
  if (canSpeak()) window.speechSynthesis.cancel();
}

// Markdown to something worth hearing: no code blocks, links read by their text, no symbols.
function plain(md) {
  return md
    .replace(/```[\s\S]*?```/g, " (code left out) ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/[*_>#|~]/g, "")
    .replace(/https?:\/\/\S+/g, "a link")
    .replace(/\s+/g, " ")
    .trim();
}

export function say(md) {
  if (!speakOn || !canSpeak()) return;
  const text = plain(md);
  if (!text) return;
  hush();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = document.documentElement.lang || navigator.language || "en";
  u.rate = 1.02;
  window.speechSynthesis.speak(u);
}

// The mic: one utterance per press. Interim words show in the line; the final text is sent.
export function listen(input, send, log) {
  if (!Recognition) return log("This browser has no speech recognition. Safari and Chrome do.", "err");
  if (rec) { rec.stop(); return; }
  hush();
  rec = new Recognition();
  rec.lang = navigator.language || "en";
  rec.interimResults = true;
  rec.continuous = false;
  const button = document.getElementById("mic");
  button?.classList.add("on");
  let finalText = "";
  rec.onresult = (ev) => {
    let interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const r = ev.results[i];
      if (r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    input.value = (finalText + interim).trim();
  };
  rec.onerror = (ev) => {
    if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
      log("The browser was not allowed to use the microphone. Allow it for this page and try again.", "err");
    } else if (ev.error !== "no-speech" && ev.error !== "aborted") {
      log(`Speech recognition stopped: ${ev.error}.`, "err");
    }
  };
  rec.onend = () => {
    button?.classList.remove("on");
    rec = null;
    if (finalText.trim()) { input.value = finalText.trim(); send(); }
  };
  rec.start();
}

load();
