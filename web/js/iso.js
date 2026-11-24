// Isometric view of the vault.
//
// Every tile is still drawn by name. A tile's `iso` block in world/tiles.json says how:
//   shape   which placeholder to draw (block, floor, box, pool, orb, flame, papers, mat, door, edge)
//   h       height in pixels at zoom 1 (walls are 56)
//   f       footprint, 0..1 of a tile
//   color   base colour; faces are shaded from it
//   sprite  optional image path; when it loads it replaces the placeholder
//   anchor  optional [x, y]: the pixel in the sprite that sits on the tile's centre
//           (default: bottom centre, i.e. the feet). Sprites can be any size.
//   scale   optional sprite scale (default 1)
//   painted true for things a painted backdrop already shows (floors, walls)
// Creatures work the same way through `creatures.<name>.iso`, and can add animations for
// states such as "thinking" under `iso.states` (see anim.js).
//
// Painted backdrops: world.backdrops in world/vault.json is a list of
//   { "image": "backdrops/hearth.png", "cells": [x, y, w, h], "at": [px, py] }
// Inside `cells`, tiles marked `painted` are not drawn; the image is drawn instead, with its
// top-left corner at `at` (pixels, relative to the centre of tile 0,0 at zoom 1). Props and
// creatures are still separate sprites on top, depth-sorted by their tile. The tile grid stays
// underneath, invisible, for walking, clicking and the map check.

import { frameOf, preload } from "./anim.js";

export const TW = 64; // tile width on screen (diamond)
export const TH = 32; // tile height on screen

const AMBIENT = [0.2, 0.17, 0.27];
// Sprites are painted with their own shading, so they take the scene's light a little brighter.
const SPRITE_GAIN = 1.6;
// Flat things are drawn with the floor; only things that stand up are depth-sorted.
const FLAT = new Set(["floor", "glint", "door", "edge", "mat", "papers", "pool"]);

// ---------- colour helpers ----------

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgb(c, light = [1, 1, 1], k = 1) {
  const r = Math.min(255, c[0] * light[0] * k);
  const g = Math.min(255, c[1] * light[1] * k);
  const b = Math.min(255, c[2] * light[2] * k);
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}

// Stable per-tile noise, so floors are not flat colour.
function hash(x, y) {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = (h ^ (h >>> 13)) * 1274126177;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

// ---------- renderer ----------

export class IsoRenderer {
  constructor(canvas, world) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.world = world;
    this.tiles = world.tiles;
    this.creatures = world.creatures;
    this.cam = null; // camera centre in world pixels
    this.zoom = 1;
    this.hover = null;
    this.images = {};
    this.colourCache = new Map();
    this.shadeCache = new Map();
    this.loadSprites();
    this.backdrops = (world.backdrops || []).map((b) => {
      const img = new Image();
      img.src = b.image;
      return { ...b, img };
    });
    this.lights = this.findLights();
  }

  loadSprites() {
    const defs = [...Object.values(this.tiles), ...Object.values(this.creatures)];
    for (const d of defs) {
      for (const src of [d.iso?.sprite, ...(d.iso?.variants || [])]) {
        if (src && !this.images[src]) {
          const img = new Image();
          img.src = src;
          this.images[src] = img;
        }
      }
    }
    for (const d of Object.values(this.creatures)) preload(d.iso?.states);
  }

  // One of the tile's `iso.variants` (or its sprite), picked by position so it never changes.
  // Repeat a path in the list to make it more common.
  variantFor(def, x, y) {
    const list = def.iso?.variants;
    if (!list?.length) return this.spriteFor(def);
    const img = this.images[list[Math.floor(hash(x, y) * list.length) % list.length]];
    return img && img.complete && img.naturalWidth ? img : this.spriteFor(def);
  }

  spriteFor(def) {
    const img = def.iso?.sprite && this.images[def.iso.sprite];
    return img && img.complete && img.naturalWidth ? img : null;
  }

  // Draw a sprite of any size with its anchor (default: bottom centre) on the given point.
  // With `light`, the sprite is shaded by the light on its tile, like the placeholders.
  drawSprite(img, iso, sx, sy, defaultAnchor, alpha = 1, light = null, src = null) {
    const k = iso.scale || 1;
    const [ax, ay] = iso.anchor || defaultAnchor;
    const [x0, y0, w, h] = src || [0, 0, img.naturalWidth, img.naturalHeight];
    const shaded = light && iso.lit !== false ? this.shade(img, light, src) : null;
    this.ctx.globalAlpha = alpha;
    if (shaded) this.ctx.drawImage(shaded, sx - ax * k, sy - ay * k, w * k, h * k);
    else this.ctx.drawImage(img, x0, y0, w, h, sx - ax * k, sy - ay * k, w * k, h * k);
    this.ctx.globalAlpha = 1;
  }

  // A copy of the sprite multiplied by the tile's light, cached by (sprite, light level).
  shade(img, light, src) {
    const q = light.map((v) => Math.min(16, Math.round(Math.min(1, v * SPRITE_GAIN) * 16)));
    const key = `${img.src}|${src || ""}|${q.join(",")}`;
    let out = this.shadeCache.get(key);
    if (out) return out;
    if (this.shadeCache.size > 4000) this.shadeCache.clear();
    const [x0, y0, w, h] = src || [0, 0, img.naturalWidth, img.naturalHeight];
    out = document.createElement("canvas");
    out.width = w;
    out.height = h;
    const o = out.getContext("2d");
    o.imageSmoothingEnabled = false;
    o.drawImage(img, x0, y0, w, h, 0, 0, w, h);
    o.globalCompositeOperation = "multiply";
    o.fillStyle = `rgb(${q.map((v) => (v * 16) | 0).join(",")})`;
    o.fillRect(0, 0, w, h);
    o.globalCompositeOperation = "destination-in"; // keep the sprite's own transparency
    o.drawImage(img, x0, y0, w, h, 0, 0, w, h);
    this.shadeCache.set(key, out);
    return out;
  }

  // True when a loaded painted backdrop covers this tile.
  paintedOver(x, y) {
    return this.backdrops.some((b) => {
      const [bx, by, bw, bh] = b.cells;
      return b.img.complete && b.img.naturalWidth && x >= bx && x < bx + bw && y >= by && y < by + bh;
    });
  }

  col(hex) {
    if (!this.colourCache.has(hex)) this.colourCache.set(hex, hexToRgb(hex));
    return this.colourCache.get(hex);
  }

  isoOf(id) {
    return this.tiles[id]?.iso || { shape: "floor", color: "#1a1424" };
  }

  // Light sources come from the map: anything whose iso block has a `light`.
  findLights() {
    const { map, legend } = this.world;
    const lights = [];
    for (let y = 0; y < map.length; y++) {
      for (let x = 0; x < map[y].length; x++) {
        const iso = this.isoOf(legend[map[y][x]]);
        if (iso.light) lights.push({ x, y, ...iso.light, rgb: hexToRgb(iso.light.color) });
      }
    }
    return lights;
  }

  lightAt(x, y, t, extra) {
    const out = [...AMBIENT];
    const add = (l, flicker = 1) => {
      const d = Math.hypot(l.x - x, l.y - y);
      if (d > l.radius) return;
      const k = l.strength * flicker * (1 - d / l.radius) ** 1.6;
      out[0] += (l.rgb[0] / 255) * k;
      out[1] += (l.rgb[1] / 255) * k;
      out[2] += (l.rgb[2] / 255) * k;
    };
    for (const l of this.lights) {
      const flicker = l.flicker ? 0.85 + 0.15 * Math.sin(t * 11 + l.x * 3.1) * Math.sin(t * 7.3 + l.y) : 1;
      add(l, flicker);
    }
    for (const l of extra) add(l);
    return out;
  }

  resize(box) {
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.floor(box.width);
    this.h = Math.floor(box.height);
    this.canvas.style.width = `${this.w}px`;
    this.canvas.style.height = `${this.h}px`;
    this.canvas.width = this.w * dpr;
    this.canvas.height = this.h * dpr;
    this.dpr = dpr;
  }

  // grid -> world pixels (centre of the tile's diamond)
  static toScreen(x, y) {
    return [(x - y) * (TW / 2), (x + y) * (TH / 2)];
  }

  // canvas pixel -> grid cell
  pick(mx, my) {
    if (!this.origin) return null;
    const wx = (mx - this.origin[0]) / this.zoom;
    const wy = (my - this.origin[1]) / this.zoom;
    const gx = Math.round((wx / (TW / 2) + wy / (TH / 2)) / 2);
    const gy = Math.round((wy / (TH / 2) - wx / (TW / 2)) / 2);
    return { x: gx, y: gy };
  }

  // ---------- primitives ----------

  diamond(cx, cy, w, h) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(cx, cy - h / 2);
    c.lineTo(cx + w / 2, cy);
    c.lineTo(cx, cy + h / 2);
    c.lineTo(cx - w / 2, cy);
    c.closePath();
  }

  // An upright box on the tile: top, south-west face and south-east face.
  box(cx, cy, f, height, base, light, alpha = 1) {
    const c = this.ctx;
    const w = TW * f, h = TH * f;
    const L = [cx - w / 2, cy], R = [cx + w / 2, cy], B = [cx, cy + h / 2];
    c.globalAlpha = alpha;
    // left face
    c.fillStyle = rgb(base, light, 0.8);
    c.beginPath();
    c.moveTo(...L); c.lineTo(...B); c.lineTo(B[0], B[1] - height); c.lineTo(L[0], L[1] - height);
    c.closePath(); c.fill();
    // right face
    c.fillStyle = rgb(base, light, 0.55);
    c.beginPath();
    c.moveTo(...B); c.lineTo(...R); c.lineTo(R[0], R[1] - height); c.lineTo(B[0], B[1] - height);
    c.closePath(); c.fill();
    // top
    c.fillStyle = rgb(base, light, 1.25);
    this.diamond(cx, cy - height, w, h);
    c.fill();
    c.globalAlpha = 1;
  }

  glow(cx, cy, r, colour, a) {
    const c = this.ctx;
    const g = c.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, `rgba(${colour[0]},${colour[1]},${colour[2]},${a})`);
    g.addColorStop(1, `rgba(${colour[0]},${colour[1]},${colour[2]},0)`);
    c.fillStyle = g;
    c.beginPath();
    c.arc(cx, cy, r, 0, Math.PI * 2);
    c.fill();
  }

  // ---------- tiles ----------

  drawFloor(x, y, sx, sy, iso, light) {
    const c = this.ctx;
    // a floor sprite (64x32 diamond) replaces the drawn floor everywhere, glints included
    const glint = this.spriteFor(this.tiles.floor_glint || {});
    const floor = (iso.shape === "glint" && glint) || this.variantFor(this.tiles.floor || {}, x, y);
    if (floor) {
      // a slight per-tile difference in brightness, so the floor never looks stamped
      const k = 0.93 + 0.14 * hash(x * 3 + 1, y * 7 + 2);
      this.drawSprite(floor, {}, sx, sy, [floor.naturalWidth / 2, floor.naturalHeight / 2], 1, light.map((v) => v * k));
      return;
    }
    const n = hash(x, y);
    const base = this.col(iso.floor || "#1d1628");
    c.fillStyle = rgb(base, light, 0.85 + n * 0.3);
    this.diamond(sx, sy, TW + 1, TH + 1);
    c.fill();
    c.strokeStyle = rgb(base, light, 0.6);
    c.lineWidth = 1;
    this.diamond(sx, sy, TW, TH);
    c.stroke();
  }

  drawTile(x, y, ch, id, t, light, cut) {
    const c = this.ctx;
    const iso = this.isoOf(id);
    const [sx, sy] = IsoRenderer.toScreen(x, y);
    const base = this.col(iso.color || "#333");
    // part of a bigger object whose sprite is drawn on another tile (the ends of the desk)
    if (iso.hideWhenSprite && this.spriteFor(this.tiles[iso.hideWhenSprite] || {})) return;
    const sprite = this.spriteFor(this.tiles[id] || {});
    if (sprite) {
      this.drawSprite(sprite, iso, sx, sy, [sprite.naturalWidth / 2, sprite.naturalHeight - TH / 2], cut ? 0.3 : 1, light);
      if (iso.shape === "flame") {
        const flick = 0.8 + 0.2 * Math.sin(t * 13 + x) * Math.sin(t * 9 + y);
        this.glow(sx, sy - (iso.glowAt ?? (iso.h || 14) + 6), 26 * flick, [255, 150, 60], 0.45);
      }
      return;
    }

    const h = iso.h || 0;
    const alpha = cut ? 0.28 : 1;
    const height = cut ? Math.min(h, 10) : h;

    switch (iso.shape) {
      case "block":
        this.box(sx, sy, 1, height, base, light, cut ? 0.55 : 1);
        if (!cut) {
          // a faint glassy streak on the obsidian
          c.strokeStyle = rgb(base, light, 2.2);
          c.globalAlpha = 0.15 + 0.1 * hash(x, y);
          c.beginPath();
          c.moveTo(sx - TW / 4, sy - height * 0.8);
          c.lineTo(sx - TW / 6, sy - height * 0.2);
          c.stroke();
          c.globalAlpha = 1;
        }
        break;

      case "box": {
        this.box(sx, sy, iso.f || 0.8, height, base, light, alpha);
        const stripe = this.world.shelfColour?.(x, y) || iso.stripe;
        if (stripe && !cut) {
          // rows of book spines on the visible faces
          const s = this.col(stripe);
          const w = TW * (iso.f || 0.8), hh = TH * (iso.f || 0.8);
          for (let i = 1; i <= 3; i++) {
            const yy = sy - (height * i) / 4;
            c.strokeStyle = rgb(s, light, 0.9);
            c.lineWidth = 3;
            c.beginPath();
            c.moveTo(sx - w / 2 + 3, yy - 1);
            c.lineTo(sx - 2, yy + hh / 2 - 3);
            c.stroke();
            c.strokeStyle = rgb(s, light, 0.6);
            c.beginPath();
            c.moveTo(sx + 2, yy + hh / 2 - 3);
            c.lineTo(sx + w / 2 - 3, yy - 1);
            c.stroke();
          }
          c.lineWidth = 1;
        }
        if (iso.ledger && !cut) {
          c.fillStyle = rgb([230, 215, 180], light, 1);
          this.diamond(sx, sy - height - 1, TW * 0.35, TH * 0.35);
          c.fill();
        }
        break;
      }

      case "flame": {
        this.box(sx, sy, iso.f || 0.35, height, base, light);
        const flick = 0.8 + 0.2 * Math.sin(t * 13 + x) * Math.sin(t * 9 + y);
        this.glow(sx, sy - height - 6, 26 * flick, [255, 150, 60], 0.55);
        c.fillStyle = `rgba(255,${190 + 40 * flick | 0},110,0.95)`;
        c.beginPath();
        c.ellipse(sx, sy - height - 6, 4, 8 * flick, 0, 0, Math.PI * 2);
        c.fill();
        break;
      }

      case "pool": {
        this.drawFloor(x, y, sx, sy, iso, light);
        c.fillStyle = rgb(base, light, 0.9);
        this.diamond(sx, sy + 2, TW * 0.92, TH * 0.92);
        c.fill();
        const shine = 0.5 + 0.5 * Math.sin(t * 1.3 + x * 1.7 + y * 2.1);
        c.strokeStyle = `rgba(140,180,255,${0.15 + 0.25 * shine})`;
        c.beginPath();
        c.moveTo(sx - 14, sy + 2);
        c.quadraticCurveTo(sx, sy - 3 + shine * 4, sx + 14, sy + 2);
        c.stroke();
        break;
      }

      case "orb": {
        const float = 22 + 3 * Math.sin(t * 1.4 + x + y);
        const pulse = 0.7 + 0.3 * Math.sin(t * 1.5 + x * 1.7 + y * 2.3);
        c.fillStyle = "rgba(0,0,0,0.35)";
        c.beginPath();
        c.ellipse(sx, sy, 9, 4.5, 0, 0, Math.PI * 2);
        c.fill();
        this.glow(sx, sy - float, 22, base, 0.45 * pulse);
        c.fillStyle = rgb(base, [1, 1, 1], 1.1 + 0.3 * pulse);
        c.beginPath();
        c.arc(sx, sy - float, 5, 0, Math.PI * 2);
        c.fill();
        break;
      }

      case "edge": {
        // thin light along the link direction, drawn on the floor
        const dir = { "-": [1, 0], "|": [0, 1], "\\": [1, 1], "/": [1, -1] }[ch] || [1, 0];
        const [ex, ey] = IsoRenderer.toScreen(dir[0] / 2, dir[1] / 2);
        c.strokeStyle = rgb(base, [1, 1, 1], 1);
        c.globalAlpha = 0.6;
        c.lineWidth = 2;
        c.beginPath();
        c.moveTo(sx - ex, sy - ey);
        c.lineTo(sx + ex, sy + ey);
        c.stroke();
        c.globalAlpha = 1;
        c.lineWidth = 1;
        break;
      }

      case "papers": {
        for (let i = 0; i < 3; i++) {
          const ox = (hash(x + i, y) - 0.5) * 30, oy = (hash(x, y + i) - 0.5) * 12;
          c.save();
          c.translate(sx + ox, sy + oy);
          c.rotate((hash(x * i, y) - 0.5) * 1.5);
          c.scale(1, 0.5);
          c.fillStyle = rgb(base, light, 1);
          c.fillRect(-6, -8, 12, 16);
          c.restore();
        }
        break;
      }

      case "mat":
        c.fillStyle = rgb(base, light, 1);
        this.diamond(sx, sy, TW * 0.7, TH * 0.7);
        c.fill();
        break;

      case "door": {
        c.fillStyle = rgb(base, light, 0.9);
        this.diamond(sx, sy, TW * 0.8, TH * 0.8);
        c.fill();
        break;
      }

      case "glint": {
        const tw = 0.5 + 0.5 * Math.sin(t * 2 + hash(x, y) * 20);
        this.glow(sx + (hash(x, y) - 0.5) * 20, sy, 6, base, 0.5 * tw);
        break;
      }

      default:
        break;
    }
  }

  // ---------- creatures ----------

  drawCreature(e, t, light) {
    const c = this.ctx;
    const def = this.creatures[e.kind];
    const iso = def.iso || {};
    const [sx, sy] = IsoRenderer.toScreen(e.rx, e.ry);
    const base = this.col(iso.color || def.fg || "#ccc");

    // selection ring under the figure
    if (iso.ring) {
      c.strokeStyle = iso.ring;
      c.globalAlpha = 0.85;
      c.lineWidth = 2;
      c.beginPath();
      c.ellipse(sx, sy, 15, 7.5, 0, 0, Math.PI * 2);
      c.stroke();
      c.globalAlpha = 1;
      c.lineWidth = 1;
    }
    c.fillStyle = "rgba(0,0,0,0.4)";
    c.beginPath();
    c.ellipse(sx, sy, 10, 5, 0, 0, Math.PI * 2);
    c.fill();

    // an animation for the current state (e.g. "thinking") wins over the still sprite
    const slot = e.state && iso.states?.[e.state];
    const fr = frameOf(slot, t - (e.stateSince ?? t));
    if (fr) {
      const k = slot.scale ?? iso.scale ?? 1;
      const [ax, ay] = slot.anchor || iso.anchor || [fr.sw / 2, fr.sh];
      c.drawImage(fr.img, fr.sx, fr.sy, fr.sw, fr.sh, sx - ax * k, sy - ay * k, fr.sw * k, fr.sh * k);
      return;
    }
    const sprite = this.spriteFor(def);
    if (sprite) {
      const k = iso.scale || 1;
      const bob = Math.sin(t * 2 + e.rx) * 0.6;
      this.drawSprite(sprite, iso, sx, sy - (e.moving ? 0 : bob), [sprite.naturalWidth / 2, sprite.naturalHeight], e.alpha ?? 1, light);
      const head = sy - (iso.anchor ? iso.anchor[1] : sprite.naturalHeight) * k;
      if (iso.glow && e.state && e.state !== "idle") {
        const g = e.state === "thinking" ? 0.18 + 0.15 * Math.sin(t * 3) : 0.25;
        this.glow(sx, head + 10, 20, this.col(iso.glow), g);
      }
      if (e.state === "thinking") this.thinkingMotes(sx, head - 4, t - (e.stateSince ?? t), this.col(iso.glow || "#ffffff"));
      return;
    }

    // placeholder figure: robe, hood or head
    const bob = e.moving ? Math.abs(Math.sin(t * 14)) * 2 : Math.sin(t * 2 + e.rx) * 0.6;
    const top = sy - 34 - bob;
    c.globalAlpha = e.alpha ?? 1;
    c.fillStyle = rgb(base, light, 0.75);
    c.beginPath();
    c.moveTo(sx - 9, sy - 2);
    c.lineTo(sx + 9, sy - 2);
    c.lineTo(sx + 5, top + 12);
    c.lineTo(sx - 5, top + 12);
    c.closePath();
    c.fill();
    c.fillStyle = rgb(base, light, 0.5);
    c.beginPath();
    c.moveTo(sx + 2, sy - 2);
    c.lineTo(sx + 9, sy - 2);
    c.lineTo(sx + 5, top + 12);
    c.lineTo(sx + 1, top + 12);
    c.closePath();
    c.fill();
    if (iso.hood) {
      c.fillStyle = rgb(base, light, 0.65);
      c.beginPath();
      c.moveTo(sx, top - 4);
      c.lineTo(sx + 7, top + 13);
      c.lineTo(sx - 7, top + 13);
      c.closePath();
      c.fill();
      c.fillStyle = "rgba(10,8,16,0.9)";
      c.beginPath();
      c.arc(sx, top + 9, 3.2, 0, Math.PI * 2);
      c.fill();
      if (iso.glow) {
        // two points of light in the hood; brighter while Tally thinks or speaks
        const eyes = e.state === "thinking" ? 0.55 + 0.45 * Math.sin(t * 5) : e.state === "speaking" ? 1 : 0.6;
        c.fillStyle = iso.glow;
        c.globalAlpha = (e.alpha ?? 1) * eyes;
        for (const dx of [-1.3, 1.3]) c.fillRect(sx + dx - 0.6, top + 8.4, 1.2, 1);
        c.globalAlpha = e.alpha ?? 1;
      }
    } else {
      c.fillStyle = rgb(this.col(iso.skin || "#d9b48a"), light, 1);
      c.beginPath();
      c.arc(sx, top + 7, 5, 0, Math.PI * 2);
      c.fill();
    }
    if (iso.glow) {
      const g = e.state === "thinking" ? 0.25 + 0.2 * Math.sin(t * 3) : e.state === "speaking" ? 0.4 : 0.25;
      this.glow(sx, top + 10, 18, this.col(iso.glow), g * (e.alpha ?? 1));
    }
    if (e.state === "thinking") this.thinkingMotes(sx, top - 6, t - (e.stateSince ?? t), this.col(iso.glow || "#ffffff"));
    c.globalAlpha = 1;
  }

  // Placeholder "thinking" animation: three ink motes drifting up from the hood.
  thinkingMotes(x, y, el, col) {
    const c = this.ctx;
    for (let i = 0; i < 3; i++) {
      const p = (el * 0.8 + i / 3) % 1; // each mote's progress through its rise
      const a = Math.sin(p * Math.PI) * Math.min(1, el * 2);
      c.fillStyle = `rgba(${col.join(",")},${(0.85 * a).toFixed(3)})`;
      c.beginPath();
      c.arc(x + Math.sin((p + i) * 5) * 4 + (i - 1) * 3, y - p * 16, 1.6 - p * 0.6, 0, Math.PI * 2);
      c.fill();
    }
  }

  // ---------- frame ----------

  draw(world, entities, t, player) {
    const c = this.ctx;
    const { map, legend } = world;

    // camera eases toward the player
    const [px, py] = IsoRenderer.toScreen(player.rx, player.ry);
    if (!this.cam) this.cam = [px, py];
    this.cam[0] += (px - this.cam[0]) * 0.12;
    this.cam[1] += (py - this.cam[1]) * 0.12;
    this.origin = [this.w / 2 - this.cam[0] * this.zoom, this.h / 2 - this.cam[1] * this.zoom + 20];

    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = "#050308";
    c.fillRect(0, 0, this.w, this.h);
    c.setTransform(this.dpr * this.zoom, 0, 0, this.dpr * this.zoom, this.dpr * this.origin[0], this.dpr * this.origin[1]);
    c.imageSmoothingEnabled = false; // pixel-art sprites stay crisp when zoomed

    // moving lights: Kristian carries a small lantern, Tally a faint glow
    const extra = entities.map((e) => {
      const l = this.creatures[e.kind].iso?.light;
      return l ? { x: e.rx, y: e.ry, ...l, rgb: hexToRgb(l.color) } : null;
    }).filter(Boolean);

    const light = [];
    for (let y = 0; y < map.length; y++) {
      light.push([]);
      for (let x = 0; x < map[y].length; x++) light[y].push(this.lightAt(x, y, t, extra));
    }

    // 0. painted backdrops, if any have been added
    for (const b of this.backdrops) {
      if (b.img.complete && b.img.naturalWidth) c.drawImage(b.img, b.at[0], b.at[1]);
    }

    // 1. floors under everything (so walls and objects sort cleanly on top)
    for (let y = 0; y < map.length; y++) {
      for (let x = 0; x < map[y].length; x++) {
        const id = legend[map[y][x]];
        if (!id) continue;
        const iso = this.isoOf(id);
        if (iso.shape === "block" || this.paintedOver(x, y)) continue;
        const [sx, sy] = IsoRenderer.toScreen(x, y);
        this.drawFloor(x, y, sx, sy, iso, light[y][x]);
        if (iso.shape !== "floor" && FLAT.has(iso.shape)) this.drawTile(x, y, map[y][x], id, t, light[y][x], false);
      }
    }

    // 2. everything that stands up, back to front
    const items = [];
    for (let y = 0; y < map.length; y++) {
      for (let x = 0; x < map[y].length; x++) {
        const id = legend[map[y][x]];
        if (!id || FLAT.has(this.isoOf(id).shape)) continue;
        if (this.isoOf(id).painted && this.paintedOver(x, y)) continue;
        items.push({ depth: x + y, x, kind: "tile", y, ch: map[y][x], id });
      }
    }
    for (const e of entities) items.push({ depth: e.rx + e.ry + 0.5, x: e.rx, kind: "creature", e });
    items.sort((a, b) => a.depth - b.depth || a.x - b.x);

    const pd = player.rx + player.ry, pc = player.rx - player.ry;
    for (const it of items) {
      if (it.kind === "creature") {
        const lx = Math.round(it.e.rx), ly = Math.round(it.e.ry);
        this.drawCreature(it.e, t, light[ly]?.[lx] || AMBIENT);
        continue;
      }
      const iso = this.isoOf(it.id);
      // Tall things in front of Kristian are cut down so he never disappears behind a wall.
      const ahead = it.depth - pd;
      const cut = (iso.h || 0) > 20 && ahead > 0 && ahead <= 7 && Math.abs(it.x - it.y - pc) <= 3 + ahead * 0.5;
      this.drawTile(it.x, it.y, it.ch, it.id, t, light[it.y][it.x], cut);
    }

    // 3. hover outline
    if (this.hover && map[this.hover.y]?.[this.hover.x]) {
      const [hx, hy] = IsoRenderer.toScreen(this.hover.x, this.hover.y);
      c.strokeStyle = "rgba(255,224,102,0.6)";
      c.lineWidth = 1.5;
      this.diamond(hx, hy, TW, TH);
      c.stroke();
      c.lineWidth = 1;
    }

    // 4. vignette
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const g = c.createRadialGradient(this.w / 2, this.h / 2, Math.min(this.w, this.h) * 0.3, this.w / 2, this.h / 2, Math.max(this.w, this.h) * 0.75);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,0.75)");
    c.fillStyle = g;
    c.fillRect(0, 0, this.w, this.h);
  }
}
