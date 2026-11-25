// Animation slots for creatures and portraits.
//
// A creature (in world/tiles.json) can name animations for its states under `iso.states`,
// and its portrait under `portrait.states`. The states the game uses today:
//   "thinking"  Tally is working on a reply (before the first words, and while reading notes)
//   "speaking"  Tally's words are arriving
// Each slot takes either a list of frame images or one sprite sheet laid out in a row:
//   "thinking": { "frames": ["sprites/tally-think-1.png", "sprites/tally-think-2.png"], "fps": 6 }
//   "thinking": { "sheet": "sprites/tally-think.png", "frame": [96, 128], "count": 8, "fps": 8 }
// Optional: "anchor" and "scale" (as for sprites), "loop": false to hold the last frame.
// Until the images exist (or while they load), the game draws its placeholder for that state.

const cache = {};

function load(src) {
  if (!cache[src]) {
    const img = new Image();
    img.src = src;
    cache[src] = img;
  }
  const img = cache[src];
  return img.complete && img.naturalWidth ? img : null;
}

// Start loading every image a slot needs, so the first play has no gap.
export function preload(states = {}) {
  for (const s of Object.values(states)) {
    if (s?.sheet) load(s.sheet);
    for (const f of s?.frames || []) load(f);
  }
}

// The frame to show for `slot` at `elapsed` seconds into the state, or null if not ready.
// Returns { img, sx, sy, sw, sh } (the source rectangle inside the image).
export function frameOf(slot, elapsed) {
  if (!slot) return null;
  const count = slot.sheet ? slot.count || 1 : (slot.frames || []).length;
  if (!count) return null;
  let i = Math.floor(elapsed * (slot.fps || 8));
  i = slot.loop === false ? Math.min(i, count - 1) : i % count;
  if (slot.sheet) {
    const img = load(slot.sheet);
    if (!img) return null;
    const [fw, fh] = slot.frame || [img.naturalWidth / count, img.naturalHeight];
    const perRow = Math.max(1, Math.floor(img.naturalWidth / fw));
    return { img, sx: (i % perRow) * fw, sy: Math.floor(i / perRow) * fh, sw: fw, sh: fh };
  }
  const img = load(slot.frames[i]);
  return img ? { img, sx: 0, sy: 0, sw: img.naturalWidth, sh: img.naturalHeight } : null;
}
