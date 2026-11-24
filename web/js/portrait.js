// Character portraits for the talk box.
//
// Each creature in world/tiles.json can have a `portrait` block:
//   "portrait": { "image": "portraits/tally.png", "frame": "#5fe0d0" }
// When `image` is set and loads, it is shown (any size; it is scaled to fit the frame, keeping
// its proportions). Until then a painted placeholder bust is drawn on a canvas.
// `portrait.states` can add animations for states such as "thinking" (see anim.js); while one
// is playing it is drawn on the canvas in place of the still image.

import { frameOf, preload } from "./anim.js";

const W = 120, H = 150;

export class Portrait {
  constructor(el, def) {
    this.el = el;
    this.def = def;
    this.cfg = def.portrait || {};
    this.speaking = false;
    this.state = "idle";
    this.stateSince = 0;
    preload(this.cfg.states);
    el.style.setProperty("--frame", this.cfg.frame || def.fg || "#888");

    this.canvas = document.createElement("canvas");
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = W * dpr;
    this.canvas.height = H * dpr;
    this.ctx = this.canvas.getContext("2d");
    this.ctx.scale(dpr, dpr);
    el.querySelector(".art").append(this.canvas);
    el.querySelector(".name").textContent = def.name;

    if (this.cfg.image) {
      const img = new Image();
      img.alt = def.name;
      // if the image is missing or broken, the painted placeholder simply stays
      img.onerror = () => console.warn(`portrait image not found: ${this.cfg.image}`);
      img.onload = () => {
        this.canvas.hidden = true;
        el.querySelector(".art").append(img);
        this.img = img;
      };
      img.src = this.cfg.image;
    }
  }

  setSpeaking(on) {
    this.speaking = on;
    this.el.classList.toggle("speaking", on);
  }

  // "idle", "thinking" or "speaking"
  setState(state, t = performance.now() / 1000) {
    if (state === this.state) return;
    this.state = state;
    this.stateSince = t;
    this.el.classList.toggle("thinking", state === "thinking");
  }

  draw(t) {
    const c = this.ctx;
    const fr = frameOf(this.cfg.states?.[this.state], t - this.stateSince);
    if (this.img) {
      this.img.hidden = !!fr;
      this.canvas.hidden = !fr;
    }
    if (fr) {
      // fit the frame inside the portrait, keeping its proportions, like the still image
      const k = Math.max(W / fr.sw, H / fr.sh);
      const dw = fr.sw * k, dh = fr.sh * k;
      c.fillStyle = "#0a0610";
      c.fillRect(0, 0, W, H);
      c.drawImage(fr.img, fr.sx, fr.sy, fr.sw, fr.sh, (W - dw) / 2, (H - dh) / 2, dw, dh);
      return;
    }
    if (this.img) return;
    const thinking = this.state === "thinking";
    const hex = (this.def.iso && this.def.iso.color) || this.def.fg || "#888";
    const n = parseInt(hex.slice(1), 16);
    const col = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    const shade = (k, a = 1) => `rgba(${col.map((v) => Math.min(255, v * k) | 0).join(",")},${a})`;
    const breathe = Math.sin(t * 1.6) * 1.2;
    const talk = this.speaking ? Math.abs(Math.sin(t * 9)) : 0;

    // backdrop: warm light from one side, as if from a brazier
    const bg = c.createRadialGradient(W * 0.25, H * 0.35, 5, W * 0.4, H * 0.5, W);
    bg.addColorStop(0, "#3a2418");
    bg.addColorStop(1, "#0a0610");
    c.fillStyle = bg;
    c.fillRect(0, 0, W, H);

    // shoulders
    c.fillStyle = shade(0.7);
    c.beginPath();
    c.moveTo(10, H);
    c.quadraticCurveTo(18, 98 + breathe, W / 2, 92 + breathe);
    c.quadraticCurveTo(W - 18, 98 + breathe, W - 10, H);
    c.closePath();
    c.fill();
    c.fillStyle = shade(0.45);
    c.beginPath();
    c.moveTo(W / 2, 92 + breathe);
    c.quadraticCurveTo(W - 18, 98 + breathe, W - 10, H);
    c.lineTo(W / 2 + 6, H);
    c.closePath();
    c.fill();

    const hx = W / 2, hy = 62 + breathe;
    if (this.def.iso && this.def.iso.hood) {
      // hooded figure: deep hood, face in shadow, eyes catching the light
      c.fillStyle = shade(0.6);
      c.beginPath();
      c.moveTo(hx, hy - 46);
      c.quadraticCurveTo(hx + 34, hy - 30, hx + 30, hy + 34);
      c.lineTo(hx - 30, hy + 34);
      c.quadraticCurveTo(hx - 34, hy - 30, hx, hy - 46);
      c.fill();
      c.fillStyle = "#07050b";
      c.beginPath();
      c.ellipse(hx, hy + 4, 17, 23, 0, 0, Math.PI * 2);
      c.fill();
      const glow = this.def.iso.glow || "#5fe0d0";
      c.fillStyle = glow;
      c.shadowColor = glow;
      // thinking: the eyes drift to one side, as if reading, and pulse
      const el = t - this.stateSince;
      const look = thinking ? Math.sin(el * 1.3) * 3 : 0;
      c.shadowBlur = 8 + talk * 6 + (thinking ? 4 + 4 * Math.sin(el * 4) : 0);
      for (const dx of [-7, 7]) {
        c.beginPath();
        c.ellipse(hx + dx + look, hy - (thinking ? 1 : 0), 2.6, 1.6 + (Math.sin(t * 0.7) > 0.97 ? -1.4 : 0), 0, 0, Math.PI * 2);
        c.fill();
      }
      c.shadowBlur = 0;
      if (thinking) {
        // faint ink motes rising past the hood
        for (let i = 0; i < 4; i++) {
          const p = (el * 0.35 + i / 4) % 1;
          c.globalAlpha = Math.sin(p * Math.PI) * 0.7 * Math.min(1, el * 2);
          c.beginPath();
          c.arc(hx + Math.sin((p + i) * 4) * 10 + (i - 1.5) * 9, hy + 30 - p * 80, 1.8 - p, 0, Math.PI * 2);
          c.fill();
        }
        c.globalAlpha = 1;
      }
    } else {
      // bare head: skin tone lit from the left, a shadowed right side, a mouth when speaking
      const skin = (this.def.iso && this.def.iso.skin) || "#d9b48a";
      c.fillStyle = skin;
      c.beginPath();
      c.ellipse(hx, hy, 21, 27, 0, 0, Math.PI * 2);
      c.fill();
      c.fillStyle = "rgba(20,10,20,0.45)";
      c.beginPath();
      c.ellipse(hx + 9, hy + 2, 13, 25, 0, -Math.PI / 2, Math.PI / 2);
      c.fill();
      c.fillStyle = "#3a2a20";
      c.beginPath();
      c.ellipse(hx, hy - 20, 23, 12, 0, Math.PI, 0);
      c.fill();
      c.fillStyle = "#1a1014";
      for (const dx of [-8, 8]) {
        c.beginPath();
        c.ellipse(hx + dx, hy - 2, 2.5, 2, 0, 0, Math.PI * 2);
        c.fill();
      }
      c.fillStyle = "#5a2a28";
      c.beginPath();
      c.ellipse(hx, hy + 13, 5, 1 + talk * 3, 0, 0, Math.PI * 2);
      c.fill();
    }

    // vignette so the bust sits in the frame
    const v = c.createRadialGradient(W / 2, H / 2, W * 0.35, W / 2, H / 2, W * 0.85);
    v.addColorStop(0, "rgba(0,0,0,0)");
    v.addColorStop(1, "rgba(0,0,0,0.7)");
    c.fillStyle = v;
    c.fillRect(0, 0, W, H);
  }
}
