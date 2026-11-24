# The Obsidian Vault

A small ASCII game about an Obsidian vault drawn as a place. You walk in as `@`, past shelves that are the vault's theme folders, and talk to **Tally**, who keeps the ledger at the desk in the Hearth. Tally's replies come from Claude.

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

Needs Python 3.11 or newer.

```sh
cp config.example.toml config.toml   # then set tally.core_dir to your vault folder
python3 -m obsidian_vault
```

It opens http://127.0.0.1:8765 in your browser. `config.toml` is git-ignored, so your vault path stays local.

### Tally's brain

Set `brain.kind` in `config.toml`:

- `claude-code` (default when the `claude` command is installed): runs `claude -p` with your Claude Code login and resumes the same session each turn. Tally gets only read-only tools (`Read`, `Glob`, `Grep`) and runs in the vault folder, so she can look at notes when you ask but cannot change anything.
- `api`: calls the Claude API (`claude-opus-5-5`) with `ANTHROPIC_API_KEY`. Run `pip install -r requirements.txt` first.

`brain.model` picks a model; leave it empty for the default. Environment overrides: `TALLY_CORE_DIR`, `TALLY_BRAIN`, `TALLY_MODEL`, `VAULT_PORT`.

## Controls

| Key | Action |
| --- | --- |
| arrows, `wasd`, `hjkl` | move (`yubn` and Home/PgUp/End/PgDn for diagonals) |
| `t`, Enter, or walk into Tally | talk |
| Esc | step away from the desk |
| `n` | start a new conversation |

The conversation lives in the server, so reloading the page keeps it. Restart the server or press `n` for a fresh one.

## Adding sprites

Everything on the map is a named tile (`wall`, `shelf`, `desk`, `pool`...) defined in [world/tiles.json](world/tiles.json). The renderer draws a tile's sprite when it has one and its glyph otherwise, so sprites can be added one tile at a time:

1. Put images under `web/sprites/`.
2. Either point `sprites.sheet` at a sprite sheet (`"sprites/vault.png"`, with `sprites.cell` as the cell size in pixels) and give tiles `"sprite": [col, row]`, or give a tile its own image: `"sprite": "sprites/tally.png"`.

Creatures (`player`, `tally`) work the same way. The map itself is plain text in [world/vault.json](world/vault.json); its `legend` maps each character to a tile name.

## Layout

```
obsidian_vault/   Python: config, persona loading, brains (Claude Code / API), web server
context/          the game's context layer for Tally
world/            map, rooms and tile definitions
web/              the browser client (canvas renderer, input, chat)
```
