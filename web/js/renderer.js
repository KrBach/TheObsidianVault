// The flat ASCII view: draws the world as a grid of named tiles (press v to switch views).
//
// Each cell is drawn by name ("wall", "shelf", "tally"...). If the tile has a sprite and the
// sprite image has loaded, the sprite is drawn; otherwise the glyph is. So sprites can be
// added one tile at a time by editing world/tiles.json, with no changes here.

export class GlyphRenderer {
  constructor(canvas, defs) {
    this.cols = defs.map[0].length;
    this.rows = defs.map.length;
    this.hover = null;
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tiles = defs.tiles;
    this.creatures = defs.creatures;
    this.spriteCfg = defs.sprites || {};
    this.sheet = null;
    this.images = {};
    this.cell = 20;
    this.loadSprites();
  }

  loadSprites() {
    const load = (src) => {
      const img = new Image();
      img.src = src;
      return img;
    };
    if (this.spriteCfg.sheet) this.sheet = load(this.spriteCfg.sheet);
    for (const def of [...Object.values(this.tiles), ...Object.values(this.creatures)]) {
      if (typeof def.sprite === "string" && !this.images[def.sprite]) this.images[def.sprite] = load(def.sprite);
    }
  }

  pick(mx, my) {
    return { x: Math.floor(mx / this.cell), y: Math.floor(my / this.cell) };
  }

  resize(box) {
    const cols = this.cols, rows = this.rows;
    const dpr = window.devicePixelRatio || 1;
    this.cell = Math.max(10, Math.floor(Math.min(box.width / cols, box.height / rows)));
    this.canvas.style.width = `${cols * this.cell}px`;
    this.canvas.style.height = `${rows * this.cell}px`;
    this.canvas.width = cols * this.cell * dpr;
    this.canvas.height = rows * this.cell * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  spriteFor(def) {
    const s = def.sprite;
    if (!s) return null;
    if (typeof s === "string") {
      const img = this.images[s];
      return img && img.complete && img.naturalWidth ? { img, sx: 0, sy: 0, sw: img.naturalWidth, sh: img.naturalHeight } : null;
    }
    if (Array.isArray(s) && this.sheet && this.sheet.complete && this.sheet.naturalWidth) {
      const c = this.spriteCfg.cell || 16;
      return { img: this.sheet, sx: s[0] * c, sy: s[1] * c, sw: c, sh: c };
    }
    return null;
  }

  drawCell(x, y, def, { glyph, fg, bg, alpha = 1 } = {}) {
    const ctx = this.ctx, c = this.cell;
    const px = x * c, py = y * c;
    ctx.globalAlpha = 1;
    ctx.fillStyle = bg || def.bg || "#000";
    ctx.fillRect(px, py, c, c);

    const sprite = this.spriteFor(def);
    ctx.globalAlpha = alpha;
    if (sprite) {
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(sprite.img, sprite.sx, sprite.sy, sprite.sw, sprite.sh, px, py, c, c);
    } else {
      const g = glyph ?? def.glyph;
      if (g) {
        ctx.fillStyle = fg || def.fg || "#fff";
        ctx.font = `${Math.floor(c * 0.85)}px "DejaVu Sans Mono", Menlo, Consolas, monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(g, px + c / 2, py + c / 2 + 1);
      }
    }
    ctx.globalAlpha = 1;
  }

  // Small, deterministic shimmer so animated tiles do not all move in step.
  static wobble(x, y, t, speed) {
    return Math.sin(t * speed + x * 1.7 + y * 2.3);
  }

  draw(world, entities, t) {
    const { map, legend } = world;
    for (let y = 0; y < map.length; y++) {
      for (let x = 0; x < map[y].length; x++) {
        const ch = map[y][x];
        const id = legend[ch];
        if (!id) { this.drawCell(x, y, { bg: "#000" }); continue; }
        const def = this.tiles[id];
        const opts = { glyph: def.glyph === null ? ch : undefined };
        if (def.anim === "flicker") opts.alpha = 0.75 + 0.25 * GlyphRenderer.wobble(x, y, t, 9);
        if (def.anim === "pulse") opts.alpha = 0.7 + 0.3 * GlyphRenderer.wobble(x, y, t, 1.5);
        if (def.anim === "ripple") opts.glyph = GlyphRenderer.wobble(x, y, t, 1.2) > 0.6 ? "∽" : "≈";
        if (id === "shelf" && world.shelfColour) opts.fg = world.shelfColour(x, y);
        this.drawCell(x, y, def, opts);
      }
    }
    if (this.hover && map[this.hover.y]?.[this.hover.x]) {
      this.ctx.strokeStyle = "rgba(255,224,102,0.6)";
      this.ctx.strokeRect(this.hover.x * this.cell + 0.5, this.hover.y * this.cell + 0.5, this.cell - 1, this.cell - 1);
    }
    for (const e of entities) {
      const def = this.creatures[e.kind];
      const under = this.tiles[legend[map[e.y][e.x]]] || {};
      this.drawCell(e.x, e.y, def, { bg: under.bg, alpha: e.alpha ?? 1 });
    }
  }
}
