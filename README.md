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

Tally's identity is not defined in this repo. At startup the game reads the shared core (`bootstrap.md` and `soul.md`) from your own vault, then adds a thin context layer for this game ([context/the-vault-game.md](context/the-vault-game.md)): where Tally is, how long replies should be, and that this session is read-only. Nothing from the vault is copied into the repo, and the game never writes to the vault.

## Run it

Needs Python 3.9 or newer (the one that ships with macOS is fine). No packages are needed for the Claude Code brain.

```sh
cp config.example.toml config.toml   # then set tally.core_dir to your vault folder
python3 -m obsidian_vault
```

It opens http://127.0.0.1:8765 in your browser. `config.toml` is git-ignored, so your vault path stays local.

### Tally's brain

Set `brain.kind` in `config.toml`:

- `claude-code` (the default): runs `claude -p` with your Claude Code login and resumes the same session each turn. No API key and no Python packages needed. Tally gets only read-only tools (`Read`, `Glob`, `Grep`) and runs in the vault folder, so Tally can look at notes when you ask but cannot change anything. If the game says it cannot find Claude Code, run `which claude` in a terminal and put the path it prints in `config.toml` as `claude_path = "..."` under `[brain]`.
- `api`: calls the Claude API (`claude-opus-5-5`) with `ANTHROPIC_API_KEY`. Run `python3 -m pip install -r requirements.txt` first.

`brain.model` picks a model; leave it empty for the default. Environment overrides: `TALLY_CORE_DIR`, `TALLY_BRAIN`, `TALLY_MODEL`, `CLAUDE_PATH`, `VAULT_PORT`.

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
- **Portraits.** While you talk, the talk box shows Tally's and Kristian's portraits, and the speaker's lights up. Set `creatures.tally.portrait.image` (and `player`) to an image of any size; it is fitted to the 120×150 frame (portrait 4:5 works best). Until then a placeholder bust is drawn.
- **ASCII sprites.** The ASCII view takes `sprite` (a cell on `sprites.sheet`, or an image path) the same way.

Put images under `web/` (for example `web/sprites/` and `web/backdrops/`); paths are relative to it.

The map itself is plain text in [world/vault.json](world/vault.json); its `legend` maps each character to a tile name. After editing it, run `python3 -m obsidian_vault.check_map`. It checks that every floor tile and door can be reached with straight steps, that no door opens onto a dead end, that you can get within talking range of Tally, and that lamps which block movement (`"lamp": true` and `solid`) stand against a wall. GitHub runs the same check on every pull request.

## Layout

```
obsidian_vault/   Python: config, persona loading, brains (Claude Code / API), web server
context/          the game's context layer for Tally
world/            map, rooms and tile definitions
web/              the browser client: iso.js (isometric), renderer.js (ASCII), main.js (input, walking), talk.js
```
