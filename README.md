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

Want a setup like this for yourself? See **[docs/SETUP.md](docs/SETUP.md)**: the vault's core files, Claude Code, running the game, logging in, errands and GitHub access, with placeholder examples.

## Tally is the same Tally everywhere

Tally's identity is not defined in this repo. At startup the game reads the shared core (`bootstrap.md` and `soul.md`) from your own vault, then adds a thin context layer for this game ([context/the-vault-game.md](context/the-vault-game.md)): where Tally is and how long replies should be. Nothing from the vault is copied into the repo. The game writes to the vault only through the ledger (below), and only after you approve, unless you choose otherwise.

## Run it

Needs Python 3.9 or newer (the one that ships with macOS is fine). No packages are needed for the Claude Code brain.

```sh
python3 -m obsidian_vault
```

It opens http://127.0.0.1:8765 in your browser. Then open the menu (Esc) → **Settings** and set your vault folder.

### Your settings live outside the repo

Everything that belongs to you or this computer (the vault folders, your name, the time zone, the errands browser) is kept in **one local file outside the game's folder**, so a pull, a fresh download or a new release never touches it:

| system | the local settings file |
| --- | --- |
| macOS | `~/Library/Application Support/TheObsidianVault/config.toml` |
| Linux | `~/.config/TheObsidianVault/config.toml` (or under `$XDG_CONFIG_HOME`) |
| Windows | `%APPDATA%\TheObsidianVault\config.toml` |

`VAULT_CONFIG=/some/file.toml` points the game at another file. The game creates the file on first run from [config.example.toml](config.example.toml), which holds placeholders only. The terminal shows where the file is, in a short form.

- **In the game:** Esc → **Settings** sets the Private vault folder, the Work vault folder (optional) and the browser for session errands (`safari`, `chrome` or `off`). A folder is accepted only if it exists on this computer. Esc → **Character** sets your name, your companion's name (default Tally), the time zone, a portrait upload and your password. Changes are written to the local file at once, keeping its comments. Changing the vault folder starts a fresh conversation with Tally. Guests never see either window, and the server refuses them both.
- **By hand:** edit the local file: `[names]` `owner`, `companion` and `owner_portrait`; `[tally]` `core_dir`, `work_dir` and `timezone` (an IANA name such as `"Europe/Berlin"`; empty means this computer's); `[errands]` `browser`. Restart the game afterwards.
- **Moving from an older version (done once, by itself):** an old `config.toml` in the game folder is moved to the local file and left behind as `config.toml.migrated`. Names and the time zone set earlier in the game (`.vault-state/settings.json`) are folded in, and that file is renamed `settings.json.migrated`. Nothing is deleted.
- **Kept in the state folder (`.vault-state/`, git-ignored):** the password hash, the uploaded portrait, the conversation id, the ledger, backups and Tally's moves. Set `[writes] state_dir` in the local file to a folder outside the game's folder if you also want those to survive a fresh download.

The code and context files never name anyone. They say OWNER where your name goes, and the game fills in the configured names. A test (`tests/test_pii.py`) fails if a file in the repo holds a home-folder path, an email address (other than placeholders like `@example.com`) or a phone number.

### Logging in

The game opens at the door: come in as the owner (the name in `[names] owner`) or as a **guest**.

- **The owner's password:** the first time, the door asks the owner to choose one. Only a salted hash (PBKDF2) is kept, in `.vault-state/login.json`, never the password itself. To change it, run `python3 -m obsidian_vault.auth set-password`. This also logs everyone out.
- **Staying logged in:** a login lasts 30 days in that browser, across restarts of the game. Use "Leave the vault" in the menu (Esc) to log out.
- **A guest** can walk around, use the furniture, ring the bell and talk to Tally. Tally treats them as a visitor:
  - **Tally for a guest** has no private core (no `soul.md`, no `bootstrap.md`). It has only the public description in `context/tally-guest.md`, no tools at all (no notes, no web, no vault or game tools) and a conversation of its own. It runs in a scratch folder outside the vault.
  - **Closed to guests:** the ledger, the notice board and errands. Shelf labels show only as "a closed shelf", without theme names or note counts. Tally's reasons for being where they are stay hidden too.
  - **What a guest leaves behind:** nothing. A guest's visit does not move Tally for the owner's next session, does not run Tally's ticks, and is not written to the moves journal or the vault.
- **Who can reach the game:** it only listens on this machine (`127.0.0.1`), so a guest has to sit at your computer.

### Tally's brain

Set `brain.kind` in your local settings file (see above):

- `claude-code` (the default): runs `claude -p` with your Claude Code login and resumes the same session each turn. No API key and no Python packages needed. Tally's built-in tools are read-only (`Read`, `Glob`, `Grep`, plus `WebSearch` and `WebFetch` for lookups unless `brain.web = false`) and Tally runs in the vault folder, so Tally can look at notes when you ask. Set the Work vault (Esc → Settings, or `tally.work_dir`) to let Tally read work notes too (read-only; work content never goes into the Private vault). The conversation survives a restart of the game (`.vault-state/session.json`); press `n` for a fresh one. Long answers render light Markdown; press `e` or the ⤢ button to enlarge the talk box. Writing goes only through the vault tools described under "Writing to the vault". If the game says it cannot find Claude Code, run `which claude` in a terminal and put the path it prints in your local settings file as `claude_path = "..."` under `[brain]`.
- `api`: calls the Claude API (`claude-opus-5-5`) with `ANTHROPIC_API_KEY`. Run `python3 -m pip install -r requirements.txt` first.

`brain.model` picks a model; leave it empty for the default. Environment overrides: `VAULT_CONFIG`, `TALLY_CORE_DIR`, `TALLY_WORK_DIR`, `TALLY_BRAIN`, `TALLY_MODEL`, `CLAUDE_PATH`, `VAULT_PORT`. An override wins over the settings file, including what you set in the menu.

## Writing to the vault

With the Claude Code brain, Tally gets a few vault tools from a small built-in MCP server (`obsidian_vault/vault_mcp.py`, no packages needed). They follow the rules in your `bootstrap.md`:

- **append_interaction_log**: one new line at the end of the "Interaction log" in `soul.md`, timestamped (in `[tally] timezone`) and starting `Project: The Obsidian Vault · Theme: ...`. The file is re-read just before the line is added, and nothing around it is rewritten.
- **add_coaching_item**: one new line under "For the coaching thread" in `soul.md`.
- **log_failure**: one new numbered entry in `agent-failure-log.md` (Tally's own file, written like `soul.md`), as the failure-log loop in `soul.md` asks.
- **propose_edit**: replace one exact passage in a note in a theme folder.
- **propose_note**: create a new note in a theme folder.
- **vault_size**: how big `bootstrap.md` + `soul.md` are against the 35 KB guide.

Set `writes.mode` in your local settings file:

| mode | what happens |
| --- | --- |
| `review` (default) | every change waits for you in the **ledger**: a yellow `!` in the top bar shows when something waits (or press `p`); click an entry to see the diff, then approve or reject |
| `direct` | log and coaching lines are written at once; note edits and new notes still wait for you |
| `off` | nothing is written; Tally is told the session is read-only |

Rules that always hold: only `.md` files inside the vault; never `bootstrap.md`, `archive/`, `templates/` or hidden folders such as `.obsidian/`; note changes only in the folders listed in `writes.folders`. An edit is applied only if the note has not changed since Tally proposed it. Every write keeps a backup of the old file in `.vault-state/backups/` and a line in `.vault-state/journal.jsonl`, so it can be undone by hand. `.vault-state/` is git-ignored. The API brain cannot write.

soul.md is Tally's own: interaction-log lines, coaching items and `edit_soul` changes are written at once (with a backup) in both review and direct mode. Set `soul_direct = false` under `[writes]` to review those too. Note edits and new notes in the theme folders always wait for you.

Tests (against a made-up vault in a temp folder): `python3 -m unittest discover -s tests`.

## Errands, meetings and the notice board

The conversation itself stays small and read-only. Bigger jobs go out as **errands**: Tally writes a brief (`propose_errand`) and sets it on the desk, and nothing runs until you click **Approve and send** in the ledger. The game then starts a separate `claude -p` run for it in the background ([obsidian_vault/errands.py](obsidian_vault/errands.py)) and tells Tally the outcome on your next message. There are three kinds:

- **research**: reads the vaults and the web; writes nothing. The report is the result.
- **code**: changes this game in its own git worktree on a branch `errand/<id>` (never the copy you are running), runs the tests, commits and pushes that one branch. It may not push anywhere else, merge, rebase or force-push. You review it as a pull request.
- **session**: your own Claude Code setup (skills, connectors, browser) in the vault folder, with edits auto-accepted there, for things like a regular data check with your own skills. Tools that spend art credits (SpriteForge, Recraft) are blocked unless the brief carries a spending limit you approved (`spend_usd`, shown in the ledger). Which tools it may use without asking is `[errands] session_tools`; anything else is refused, since nobody is there to answer a prompt. The desktop app's built-in browser cannot reach a run the game starts, so `[errands] browser` (also in Esc → Settings) picks one that can:
  - `"safari"` (the default): **MCPSafari**, a local MCP server named `mcp-safari` in your `~/.claude.json`, bridged to its Safari extension. Its tools (`mcp__mcp-safari`) are always allowed. Keep Safari open with the extension turned on, and grant it the sites errands need (Safari asks before the extension touches a new site, and nobody is there to answer, so the run reports an ungranted site as a blocker). Krooster is skipped: you are not logged in there in Safari.
  - `"chrome"`: **Claude in Chrome** in your own Chrome. The run starts with `claude --chrome` and `mcp__claude-in-chrome` is allowed. Keep Chrome open with the extension signed in to your Claude account. (A Chrome admin policy can block the extension; that is why Safari is the default.)
  - `"off"`: no browser.

  Approving a session errand while that browser is closed fails at once with a message saying what to open (the game only checks the process list, `pgrep -x Safari`), and the errand stays on the desk to approve again. A run that still finds no browser stops and reports `BROWSER MISSING:`, and the errand is marked failed. Only the chosen browser's tools are allowed in a run.

For large tasks Tally holds a short **meeting** first (goal, what done looks like, limits, cost) and reads the brief back before queuing it. You can call one yourself, for example at the map table.

The **notice board** (`b`, or the board button in the top bar) hangs in the Entrance Hall, by the way in, and shows errand reports, the files in the vault's `Claude outputs` folder (Markdown, images, HTML pages in a sandboxed frame) and links Tally pins with `pin_to_board`, such as a claude.ai artifact. Walk up to it, click it or right-click it to read it, or press `b` anywhere. One paper is pinned to it for each thing on the board (up to six), and a red pin means something new since you last looked. Files are served only from the folders in `[board] folders`. Errand records and logs are in `.vault-state/errands/`.

## Furniture: sitting, lying down, walking in place

Furniture says what you can do on it (`use` in [world/tiles.json](world/tiles.json): the poses `sit`, `lie` or `walk`, and the height of its surface). Stop on a piece and you take its first pose; any step gets you up. Plain floor takes sitting and lying too, so you can sit by the pool. In the vault now: a couch by the pool, chairs by the pool, in the Library, the Scriptorium and the Map Room, a hammock in the Graph Room, a bench in the Entrance Hall and a reading table. Beds, treadmills and cushions can be placed (`place_furniture`).

- You: `x` to sit (where you stand, or the nearest seat), `z` to lie down; the same key again gets you up. Walking onto a chair or the couch sits you down.
- Tally: `settle` (sit, lie or walk; an optional kind of furniture or `floor`; an optional place to go first), or `rest` in a tick. Each choice goes in Tally's moves journal like any other, and the game_state line says when either of you is sitting or lying somewhere.
- The poses are drawn as placeholders (the figure sinks onto the seat, turns on its side, or bobs on the belt) until there are sprites for them; the furniture is drawn in the same lit placeholder style as the writing desks and map table.

## Controls

| Input | Action |
| --- | --- |
| click | walk there; click Tally to walk up and talk, click a shelf to read its label |
| right-click | a menu for that spot: inspect it, look around the room, sit or lie down on furniture or the floor, ring the bell, talk to or look at Tally. What you learn appears in the text box |
| arrows, `wasd`, `hjkl` | one step along the floor grid (in the isometric view these follow the floor's diagonals) |
| `t` or Enter | talk, when next to Tally |
| Esc | step away from the desk; otherwise open the menu (settings, controls, status). `m` opens it too |
| `v` | switch between the isometric and the ASCII view |
| mouse wheel | zoom (isometric view) |
| `n` | start a new conversation |
| `e` | enlarge the talk box for long answers |
| `b` | open the notice board (replaces the `b` diagonal step) |
| `p` | open the ledger |
| `x` / `z` | sit / lie down (again to get up) |
| the notice board | on the west wall of the Entrance Hall: walk into it or click it to read it |
| 🎙 (next to the talk line) | speak instead of typing: click, talk, and pause; it sends what you said. Uses the browser's own speech recognition (Safari: Apple's dictation; Chrome: Google's servers) |
| Esc menu, "reads replies aloud" | Tally reads each reply aloud with the system voice; kept per browser |
| the bell | in the Entrance Hall, by the way in: click it (or walk into it) and Tally comes to you from wherever they are |

The conversation lives in the server and its id is saved, so reloading the page or restarting the server keeps it. Press `n` for a fresh one.

## Art

There are two views of the same map. The **isometric view** is the main one: moody and lit by braziers and candles, with walls in front of you cut down so you can always see yourself. The **ASCII view** stays as a fallback. Both draw every tile by name from [world/tiles.json](world/tiles.json).

Underneath both is an invisible tile grid. It handles walking, clicking, depth order and the map check. Art sits on top of it, so it doesn't have to fit the grid:

- **Sprites of any size.** Give a tile or creature `"iso": {"sprite": "sprites/tally.png"}`. The sprite's anchor (by default its bottom centre, the feet) is placed on the tile. Set `anchor: [x, y]` (in pixels inside the image) to move that point, and `scale` to resize. A tall shelf or a big character just reaches higher up the screen. Things are drawn back to front by grid position, so you can walk behind them.
- **Painted backdrops.** Add `backdrops` to [world/vault.json](world/vault.json): `{"image": "backdrops/hearth.png", "cells": [18, 0, 17, 9], "at": [-320, -120]}`. Inside `cells`, tiles marked `painted` (floors, walls) aren't drawn, and the image shows instead. `at` is where the image's top-left corner goes, in pixels from the centre of tile 0,0. Props and characters stay separate sprites on top.
- **Placeholders.** Until a sprite exists, the `iso` block draws a lit placeholder shape (`shape`, `h`, `f`, `color`). Anything with a `light` gives off light.
- **Portraits.** While you talk, the talk box shows Tally's and the owner's portraits, and the speaker's lights up. Set `creatures.tally.portrait.image` (and `[names] owner_portrait` for yours) to an image of any size; it is fitted to the 120×150 frame (portrait 4:5 works best). Until then a placeholder bust is drawn. Add `videos` (mp4 or webm, muted and looping) per state, e.g. `"videos": {"thinking": ["portraits/think.mp4"], "speaking": ["portraits/talk-1.mp4", "portraits/talk-2.mp4"]}`; several clips for one state take turns, and the still shows the rest of the time. Clips cross-fade in and out over the still. To make a clip loop without a visible jump (and shrink it), run `python3 -m obsidian_vault.loop_clip in.mp4 web/portraits/tally-think.mp4`; it needs ffmpeg and reports how different the first and last frames were before and after. Set `pixelated: true` for pixel-art portraits.
- **Animations.** Tally has animation slots for two states: `thinking` (working on a reply, or reading notes) and `speaking` (the words are arriving). Fill `creatures.tally.iso.states.thinking` (map) or `creatures.tally.portrait.states.thinking` (talk box) with `"frames": ["sprites/tally-think-1.png", ...]` or a sprite sheet in a row (`"sheet": "sprites/tally-think.png", "frame": [96, 128], "count": 8`), plus `fps`. Until then a placeholder plays: the eyes in Tally's hood brighten and pulse and ink motes drift up; in the portrait the eyes drift as if reading.
- **ASCII sprites.** The ASCII view takes `sprite` (a cell on `sprites.sheet`, or an image path) the same way.

Put images under `web/` (for example `web/sprites/` and `web/backdrops/`); paths are relative to it.

Tally doesn't stay at the desk: they walk between the posts listed in `tally_posts` in [world/vault.json](world/vault.json) (the desk, the Library, the Scriptorium, the Map Room), linger at each, and stay put while you are close or walking up to talk.

Every few minutes (`tally.tick_minutes`, default 6) the game also asks Tally whether they want to go somewhere or say something. The server tells Tally their coordinates, the room, what is near them, where you are and the last thing you said, and lists the named places in `spots` in [world/vault.json](world/vault.json) (the pool, the brazier, the map table, the Outbox and so on). Tally answers with a place or "stay" and an optional short line, which shows in the log, then walks there, even past you. Each tick is a separate small call ([obsidian_vault/tick.py](obsidian_vault/tick.py)) with no tools and no history, using a short sketch of Tally ([context/tally-tick.md](context/tally-tick.md)) instead of the whole core: about 4,000 tokens on Haiku (`tally.tick_model`), under a tenth of a cent. Ticks skip while you talk, while the tab is hidden, and after 30 minutes without input. Set `tick_minutes = 0` to turn them off.

Tally always knows where they are and why. What they say and where they go are tied together by game tools in the conversation: `go_to` (a place, and why), `stay_here`, `walk_with_kristian` (Tally follows a step or two behind you) and `wander`. A choice like "I'll stay here a bit" holds until the next tick, which reminds Tally where they are and why and lets them keep it or change their mind; only a promise with minutes holds past a tick. Without ticks, a choice lapses after 20 minutes and Tally goes back to their round.

Tally remembers their own moves. Every choice of where to be, from a tick or from the game tools in a conversation, is written to `.vault-state/moves.jsonl` in Tally's own words (where, why, anything said, and any small round planned with `then`). The last few are handed back to the conversation and to the next tick as Tally's own decisions, so Tally can say why they went somewhere. With ticks on there is no scripted round: Tally only moves where they chose, and stays put otherwise. With ticks off the old round between `tally_posts` still runs, and Tally is told it is the game's route, not theirs.

Tally stays where they were between sessions. The game saves Tally's position, what they are doing and why, their pose and the rest of any planned round to `.vault-state/tally.json` (every few seconds when it changes, and when the tab closes). Next time the game opens, Tally is still there, lying on the couch or halfway to the map room. Walking with the owner ends when they leave. You always come in through the front door. Delete `tally.json` to have Tally start at the desk again.

## Residents: Vellum the cat, the imp and Brass

Three residents are scripted in the browser, with no model calls, so they cost nothing ([web/js/denizens.js](web/js/denizens.js)). Both are drawn in code until they have sprites.

- **Vellum, the cat** (designed by Tally) has a mood: sleepy about half the time, otherwise curious or playful, and hungry about three hours after a meal. Sleepy, it naps by the fireplace and braziers, on its cushion in the Hearth or in the hammock; curious, it watches the pool or whoever came in last, and sits on the map table when someone works there; playful, it bats at the loose pages and the quill on a string; hungry, it waits by its bowl next to the fireplace, then goes to Tally. It sits on the desk when four or more things wait in the ledger. It never settles in or by a doorway, by the bell or on the welcome mat, and it blocks nobody. Right-click it to look at it, pet it, feed it, play with it or call it ("pspsps"): you walk over and it becomes a dialogue, with its painted portrait in Tally's place and the same choices as buttons (Esc or walking off ends it). All but calling need you next to it, and a guest may only pet and call. Tally can tend it too, with the same rules (the `tend_cat` tool, or a check-in). Where it is, its mood and when it last ate are kept in the state folder (`cat.json`), so it is where you left it. It also remembers how much it likes Tally and the owner: a meal raises that most, a game next, a pet it accepts a little (a pet it walks off from adds nothing), and it fades over days to a floor, never to nothing; guests are not remembered. Liking shifts its odds, never decides them: when curious or sleepy it more often picks a spot near the one it likes most, it is likelier to wait when they walk over and less likely to walk off mid-pet, and when very fond it sometimes tags along for a few steps or curls up by Tally's place at the desk. Tally reads it in words ("Vellum seems fond of you"), never as numbers ([obsidian_vault/cat.py](obsidian_vault/cat.py) has the rules).
- **Talking to Vellum like a 90s text adventure.** In the cat's dialogue the talk line is open too: a line starting with `>` (`> pet vellum`, `> give fish to the cat`, `> look tail`, `> pspsps`, `> help`) goes to a scripted parser ([obsidian_vault/vellum.py](obsidian_vault/vellum.py)) and never to Tally, Brass or any model, whichever talk box it was typed in, so it costs nothing. It works within two tiles of Vellum (pet, feed, play and take need you right next to it), reads verb + object with synonyms and drops filler words. The answer depends on Vellum's mood, whether it sleeps and how much it likes you (wary, friendly or fond, from the same liking as above); what actually happens (a meal, walking off, coming over, liking rising) goes through the cat's usual rules. Every word is in [world/vellum-parser.json](world/vellum-parser.json), written by Tally, and read at every line, so editing it needs no code change. A few rare answers are for fond cats only; one brings you the title (only the title) of a random note in the vault's `archive/` folder, never for a guest. A guest may use look, pet, call and talk. Tally keeps the `tend_cat` tool and does not use the parser. Whether the cat is "right next to" someone is one function on the server (`steps` in cat.py, diagonals counting as one step), used by `tend_cat`, the check-in prompt and the parser; the browser's game_state line (`catText` in main.js) counts steps the same way.
- **The imp** is the errand runner. When an errand starts, it hops off Tally's desk and runs out of the vault; when the errand is done, it comes back in with the report and pins it to the notice board.
- **Brass, the Artificer** (the game-maker) has a temporary body that follows code errands, so you can see who is busy. He lives in the vault: between jobs he potters about (his Outbox table, the braziers, the pool, the map table, the shelves), scripted, with no model calls. A queued code errand does not move him. When you approve one, the imp hops off Tally's desk and runs to fetch him from wherever he is pottering; once it reaches him it leaves by the front door, and he goes to the Outbox table and tinkers there while the errand runs (Tally's game_state says "the imp is running to fetch Brass" while it is out). Done or failed, he takes the report to the notice board, pauses, and goes back to pottering. You can talk to him: right-click him and choose "Talk to Brass". Each line is answered by one small model call (the check-in model, `tick_model`) with his soul file from the vault (`denizens/brass/brass-soul.md`), what he is working on and his recent reports; he can explain his work but not start any, which stays with errands you approve. The last few lines are kept in `brass-talk.json` in the state folder. There is one Brass: with several code errands he goes to the oldest one not yet finished with. He never stands in or by a doorway, by the bell, on the welcome mat or on the cat's cushion, and he blocks nobody. The server works out his stage from the errand records ([obsidian_vault/brass.py](obsidian_vault/brass.py)) and keeps it in `brass.json` in the state folder, so an errand running when the game restarts puts him straight back at the Outbox table. His places are `brass` in [world/vault.json](world/vault.json) (the map check tests them). Tally's game_state mentions him in one short phrase while he is in. Guests do not see him. He is drawn in code (a stocky clockmaker in a leather apron, goggles pushed up, a toolbox) until he has a sprite.

## Tally arranges the vault

The vault is Tally's to arrange. In conversation Tally can look at the map with coordinates (`look_at_map`), place or clear furniture (`place_furniture`, `clear_furniture`: candles, braziers, papers, pools, writing desks, map tables, graph nodes) and name new places to go (`name_place`). Every change goes through the same map check as the repo, so nothing can block a door or cut a room off; walls, doors, shelves, the desk and the fireplace are fixed, and a change Tally made where the built map now has something fixed is ignored. A built-in place on furniture Tally moved or cleared is dropped rather than left pointing at bare floor. Tally's changes are kept in `.vault-state/world.json` (git-ignored, so a pull never overwrites them) and laid over `world/vault.json`; `.vault-state/world-journal.jsonl` lists every change. Delete `world.json` to put the vault back as built. New rooms are still built by hand in the repo for now.

The map itself is plain text in [world/vault.json](world/vault.json); its `legend` maps each character to a tile name. After editing it, run `python3 -m obsidian_vault.check_map`. It checks that every floor tile and door can be reached with straight steps, that no door opens onto a dead end, that you can get within talking range of Tally, and that lamps which block movement (`"lamp": true` and `solid`) stand against a wall. Run it before you commit a map change.

## Layout

```
obsidian_vault/   Python: config (the local settings file), persona loading, brains (Claude Code / API), web server
context/          the game's context layer for Tally
world/            map, rooms and tile definitions
web/              the browser client: iso.js (isometric), renderer.js (ASCII), main.js (input, walking), talk.js
```
