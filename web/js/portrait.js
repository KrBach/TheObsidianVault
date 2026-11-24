// Character portraits for the talk box.
//
// Each creature in world/tiles.json can have a `portrait` block:
//   "portrait": { "image": "portraits/tally.png", "frame": "#5fe0d0" }
// When `image` is set and loads, it is shown (any size; it is scaled to fit the frame, keeping
// its proportions). Until then a painted placeholder bust is drawn on a canvas.

const W = 120, H = 150;

export class Portrait {
  constructor(el, def) {
    this.el = el;
    this.def = def;
    this.cfg = def.portrait || {};
    this.speaking = false;
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
      img.onload = () => {
        this.canvas.remove();
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

  draw(t) {
    if (this.img) return;
    const c = this.ctx;
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
      c.shadowBlur = 8 + talk * 6;
      for (const dx of [-7, 7]) {
        c.beginPath();
        c.ellipse(hx + dx, hy, 2.6, 1.6 + (Math.sin(t * 0.7) > 0.97 ? -1.4 : 0), 0, 0, Math.PI * 2);
        c.fill();
      }
      c.shadowBlur = 0;
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
