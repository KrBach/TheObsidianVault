# The Obsidian Vault

A small isometric game (with an ASCII view as a fallback) about an Obsidian vault drawn as a place. You walk in, past shelves that are the vault's theme folders, and talk to **Tally**, who keeps the ledger at the desk in the Hearth. Tally's replies come from Claude.

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
▓ ≡≡≡≡≡    ≡≡≡≡≡  ▓  ☼          ☼  ▓
▓                 ▓                ▓
▓ ≡≡≡≡≡    ≡≡≡≡≡  ▓       ππ       ▓
▓                 ▒       πτ       ▒
▓ ≡≡≡≡≡    ≡≡≡≡≡  ▓       @        ▓
```

## Tally is the same Tally everywhere

Tally's identity is not defined in this repo. At startup the game reads the shared core (`bootstrap.md` and `soul.md`) from your own vault, then adds a thin context layer for this game ([context/the-vault-game.md](context/the-vault-game.md)): where Tally is and how long replies should be. Nothing from the vault is copied into the repo. The game writes to the vault only through the ledger (below), and only after you approve, unless you choose otherwise.

## Run it

Needs Python 3.9 or newer (the one that ships with macOS is fine). No packages are needed for the Claude Code brain.

```sh
cp config.example.toml config.toml   # then set tally.core_dir to your vault folder
python3 -m obsidian_vault
```

It opens http://127.0.0.1:8765 in your browser. `config.toml` is git-ignored, so your vault path stays local.

### Tally's brain

Set `brain.kind` in `config.toml`:

- `claude-code` (the default): runs `claude -p` with your Claude Code login and resumes the same session each turn. No API key and no Python packages needed. Tally's built-in tools are read-only (`Read`, `Glob`, `Grep`) and Tally runs in the vault folder, so Tally can look at notes when you ask. Writing goes only through the vault tools described under "Writing to the vault". If the game says it cannot find Claude Code, run `which claude` in a terminal and put the path it prints in `config.toml` as `claude_path = "..."` under `[brain]`.
- `api`: calls the Claude API (`claude-opus-5-5`) with `ANTHROPIC_API_KEY`. Run `python3 -m pip install -r requirements.txt` first.

`brain.model` picks a model; leave it empty for the default. Environment overrides: `TALLY_CORE_DIR`, `TALLY_BRAIN`, `TALLY_MODEL`, `CLAUDE_PATH`, `VAULT_PORT`.

## Writing to the vault

With the Claude Code brain, Tally gets a few vault tools from a small built-in MCP server (`obsidian_vault/vault_mcp.py`, no packages needed). They follow the rules in your `bootstrap.md`:

- **append_interaction_log**: one new line at the end of the "Interaction log" in `soul.md`, timestamped (Europe/Copenhagen) and starting `Project: The Obsidian Vault · Theme: ...`. The file is re-read just before the line is added, and nothing around it is rewritten.
- **add_coaching_item**: one new line under "For the coaching thread" in `soul.md`.
- **propose_edit**: replace one exact passage in a note in a theme folder.
- **propose_note**: create a new note in a theme folder.
- **vault_size**: how big `bootstrap.md` + `soul.md` are against the 35 KB guide.

Set `writes.mode` in `config.toml`:

| mode | what happens |
| --- | --- |
| `review` (default) | every change waits for you in the **ledger** panel; click one to see the diff, then approve or reject |
| `direct` | log and coaching lines are written at once; note edits and new notes still wait for you |
| `off` | nothing is written; Tally is told the session is read-only |

Rules that always hold: only `.md` files inside the vault; never `bootstrap.md`, `archive/`, `templates/` or hidden folders such as `.obsidian/`; note changes only in the folders listed in `writes.folders`. An edit is applied only if the note has not changed since Tally proposed it. Every write keeps a backup of the old file in `.vault-state/backups/` and a line in `.vault-state/journal.jsonl`, so it can be undone by hand. `.vault-state/` is git-ignored. The API brain cannot write.

Tests (against a made-up vault in a temp folder): `python3 -m unittest discover -s tests`.

## Controls

| Input | Action |
| --- | --- |
| click | walk there; click Tally to walk up and talk, click a shelf to read its label |
| arrows, `wasd`, `hjkl` | one step along the floor grid (in the isometric view these follow the floor's diagonals) |
| `t` or Enter | talk, when next to Tally |
| Esc | step away from the desk |
| `v` | switch between the isometric and the ASCII view |
| mouse wheel | zoom (isometric view) |
| `n` | start a new conversation |

The conversation lives in the server, so reloading the page keeps it. Restart the server or press `n` for a fresh one.

## Art

There are two views of the same map. The **isometric view** is the main one: moody and lit by braziers and candles, with walls in front of you cut down so you can always see yourself. The **ASCII view** stays as a fallback. Both draw every tile by name from [world/tiles.json](world/tiles.json).

Underneath both is an invisible tile grid. It handles walking, clicking, depth order and the map check. Art sits on top of it, so it doesn't have to fit the grid:

- **Sprites of any size.** Give a tile or creature `"iso": {"sprite": "sprites/tally.png"}`. The sprite's anchor (by default its bottom centre, the feet) is placed on the tile. Set `anchor: [x, y]` (in pixels inside the image) to move that point, and `scale` to resize. A tall shelf or a big character just reaches higher up the screen. Things are drawn back to front by grid position, so you can walk behind them.
- **Painted backdrops.** Add `backdrops` to [world/vault.json](world/vault.json): `{"image": "backdrops/hearth.png", "cells": [18, 0, 17, 9], "at": [-320, -120]}`. Inside `cells`, tiles marked `painted` (floors, walls) aren't drawn, and the image shows instead. `at` is where the image's top-left corner goes, in pixels from the centre of tile 0,0. Props and characters stay separate sprites on top.
- **Placeholders.** Until a sprite exists, the `iso` block draws a lit placeholder shape (`shape`, `h`, `f`, `color`). Anything with a `light` gives off light.
- **Portraits.** While you talk, the talk box shows Tally's and Kristian's portraits, and the speaker's lights up. Set `creatures.tally.portrait.image` (and `player`) to an image of any size; it is fitted to the 120×150 frame (portrait 4:5 works best). Until then a placeholder bust is drawn. Add `videos` (mp4 or webm, muted and looping) per state, e.g. `"videos": {"thinking": ["portraits/think.mp4"], "speaking": ["portraits/talk-1.mp4", "portraits/talk-2.mp4"]}`; several clips for one state take turns, and the still shows the rest of the time. Set `pixelated: true` for pixel-art portraits.
- **Animations.** Tally has animation slots for two states: `thinking` (working on a reply, or reading notes) and `speaking` (the words are arriving). Fill `creatures.tally.iso.states.thinking` (map) or `creatures.tally.portrait.states.thinking` (talk box) with `"frames": ["sprites/tally-think-1.png", ...]` or a sprite sheet in a row (`"sheet": "sprites/tally-think.png", "frame": [96, 128], "count": 8`), plus `fps`. Until then a placeholder plays: the eyes in Tally's hood brighten and pulse and ink motes drift up; in the portrait the eyes drift as if reading.
- **ASCII sprites.** The ASCII view takes `sprite` (a cell on `sprites.sheet`, or an image path) the same way.

Put images under `web/` (for example `web/sprites/` and `web/backdrops/`); paths are relative to it.

The map itself is plain text in [world/vault.json](world/vault.json); its `legend` maps each character to a tile name. After editing it, run `python3 -m obsidian_vault.check_map`. It checks that every floor tile and door can be reached with straight steps, that no door opens onto a dead end, that you can get within talking range of Tally, and that lamps which block movement (`"lamp": true` and `solid`) stand against a wall. Run it before you commit a map change.

## Layout

```
obsidian_vault/   Python: config, persona loading, brains (Claude Code / API), web server
context/          the game's context layer for Tally
world/            map, rooms and tile definitions
web/              the browser client: iso.js (isometric), renderer.js (ASCII), main.js (input, walking), talk.js
```
