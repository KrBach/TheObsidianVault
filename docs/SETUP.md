# Set up your own vault and assistant

This guide is for anyone who wants a setup like this one: an AI assistant with one stable identity that you can reach from Claude Code, the Claude apps and a small game where you walk into your notes and talk to it.

The repo started as one person's working setup and is shared as an example. Nothing personal is in the code: you set your own name, your companion's name (default **Tally**), your vault folders and your portrait in the game's menu, and they are kept in a settings file on your computer, outside the repo (see [Run the game](#4-run-the-game) and [Make it yours](#7-make-it-yours)).

All paths, names and folder names below are placeholders. Replace them with your own.

## What you are building

```
Obsidian vault (private, on your machine)          this repo (public)
├── bootstrap.md   how the assistant works         ├── the game (Python server + browser)
├── soul.md        who the assistant is            ├── context/  rules for the game only
├── agent-failure-log.md                           └── world/    the map and furniture
├── INDEX.md       map of your notes
└── <theme folders>/  your notes
```

- **The vault holds the assistant's identity.** Every place you use the assistant (Claude Code, a Claude Project, the game) loads the same two files, so it behaves the same everywhere. Rules for one place are layered on top and never change the core.
- **The repo holds only the game and its context layer.** Nothing from the vault is copied into it, and your settings file with the vault path lives outside it.
- **The game runs on your computer.** It uses your Claude Code login, so you need no API key.

## 1. What you need

- **A Mac or Linux computer**, with Python 3.9 or newer (`python3 --version`). Windows works through WSL.
- **[Obsidian](https://obsidian.md)** (free), or any folder of Markdown files. Obsidian just makes the vault pleasant to browse.
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)**, signed in with a Claude plan. Check that it works:
  ```sh
  claude --version
  claude -p "say hello"
  ```
- **git**, and a GitHub account if you want the assistant to work on code (step 6).

## 2. Create the vault and the core files

Make a folder for your private vault, for example `~/Vaults/Private`, and open it in Obsidian ("Open folder as vault"). Then add these files at its top level.

### bootstrap.md: how the assistant works

Procedures that apply in every session. Keep it short (a few KB), because it is loaded into every conversation. A skeleton:

```markdown
# Bootstrap

## 1. Start of a session
Read soul.md. Check INDEX.md for the theme of the conversation before answering from memory.

## 2. Where things go
- Notes live in theme folders (games/, health/, projects/ ...). Each theme has an index note.
- A new note gets frontmatter with its theme as a tag, a "Related:" line, and a link from its theme index.

## 3. Rules of conduct
- Say when you are guessing. Check before you claim something is done.
- Ask before anything that costs money or cannot be undone.

## 4. The interaction log
At the end of a session, add one line to soul.md under "Interaction log":
how the interaction went (not the work), with date, time, project and theme.
```

### soul.md: who the assistant is

The assistant's identity: name, voice, values, and what it has learned about working with you. The assistant may edit this file itself, which is how it improves over time. The game looks for these exact headings:

```markdown
# Soul

## Who I am
A name, a voice, a few sentences of character. (Appearance, if you want one in the game.)

## How I work with <you>
Preferences learned over time: reply length, tone, what to check first.

## For the coaching thread
- (items the assistant wants to raise with you later)

## Interaction log
- 2026-01-01 09:00 (UTC), Project: Setup · Theme: First session: <one line on how it went>
```

### agent-failure-log.md: the assistant's mistakes

The assistant writes a numbered entry here when it gets something wrong, so it can learn from it:

```markdown
# Agent failure log

## Entries
```

### INDEX.md and theme folders

Put one folder per theme (`games/`, `health/`, `work-notes/` ...) and an `INDEX.md` that links to each theme's index note. The game turns each top-level folder into a shelf.

Two folders have a special meaning. `archive/` and `templates/` are never written to by the game. The board folder, which defaults to `Claude outputs/`, is where finished reports and pages go for the notice board.

> **Keep secrets out of the vault**, or at least out of the files the assistant loads. `bootstrap.md` and `soul.md` are sent to Claude in every conversation.

## 3. Use the same core everywhere

- **Claude Code:** add a `CLAUDE.md` in the vault folder (or in `~/.claude/CLAUDE.md`) that says: "Read bootstrap.md and soul.md in <vault path> at the start of every session and follow them."
- **Claude apps (a Project):** add `bootstrap.md` and `soul.md` as project knowledge, or link them through a connector, and say the same in the project instructions.
- **The game:** it reads the two files itself (next step).

Rules that only apply in one place, such as "replies in the game are short", go in that place's own instructions. In the game they are in `context/the-vault-game.md`. The core files stay the same everywhere.

## 4. Run the game

```sh
git clone https://github.com/<you>/TheObsidianVault.git
cd TheObsidianVault
python3 -m obsidian_vault
```

On first run the game creates your **local settings file**, outside the game's folder, so pulling a new version or downloading a fresh copy never touches it:

- macOS: `~/Library/Application Support/TheObsidianVault/config.toml`
- Linux: `~/.config/TheObsidianVault/config.toml`
- Windows: `%APPDATA%\TheObsidianVault\config.toml`

It starts as a copy of `config.example.toml` (placeholders only) with the vault folder and your name left empty. Fill them in from the game: log in, press Esc, and choose **Settings** for the vault folders and the errands browser, and **Character** for your name. Changes are saved to the file at once. You can also edit the file by hand and restart the game. The important lines:

```toml
[names]
owner = "Robin"                   # your name, as the game and the companion use it
companion = "Tally"               # your companion's name
owner_portrait = ""               # optional: an image under web/, e.g. "portraits/me.jpg"

[tally]
core_dir = "~/Vaults/Private"     # the folder with bootstrap.md and soul.md
timezone = "Europe/Berlin"        # for log timestamps; empty = this computer's
work_dir = ""                     # optional second vault, read-only (e.g. work notes)

[brain]
kind = "claude-code"              # uses your Claude Code login
claude_path = ""                  # only if the game cannot find `claude`: paste `which claude`

[writes]
mode = "review"                   # every note change waits for your approval in the game
folders = ["games", "projects"]   # theme folders the assistant may propose changes in

[errands]
browser = "safari"                # browser for session errands: "safari", "chrome" or "off"
```

The game opens http://127.0.0.1:8765. The terminal says where the settings file is (in a short form) and whether the core files loaded. If they are missing, set the Private vault folder in Esc → Settings, which checks that the folder exists.

If you used an older version that kept `config.toml` in the game folder, the first run moves it to the local file and leaves `config.toml.migrated` behind. Names set in the game before (`.vault-state/settings.json`) are folded in too. This happens once, and nothing is deleted.

The `.vault-state/` folder in the game folder is git-ignored. It holds pending changes, backups, the conversation id, your password hash, your uploaded portrait and your login sessions. Never commit it. To keep it across a fresh download as well, point `[writes] state_dir` in the settings file at a folder outside the game.

## 5. Log in

- **First launch:** the door screen asks the owner to choose a password. Only a salted hash is stored, in `.vault-state/login.json`.
- **Changing the password:**
  ```sh
  python3 -m obsidian_vault.auth set-password
  ```
- **Guests:** they can come in without a password. Guests talk to a public version of the assistant, described in `context/tally-guest.md`. It has none of the core files, no tools and no access to the ledger, the notice board or errands.
- **Network:** the game listens only on `127.0.0.1`, so it can be reached only from your own computer. Do not change `[server] host` to expose it to a network unless you know what you are doing.

## 6. Errands, and letting the assistant work on code

Errands are bigger jobs the assistant writes out and leaves on your desk in the game. Nothing runs until you approve one in the ledger. A separate Claude Code session then does the work and reports back. There are three kinds:

- **research:** reads the vaults and the web, and writes nothing.
- **code:** changes this game in its own git worktree and branch, `errand/<id>`. It runs the tests, then commits and pushes that one branch. It never touches `main` and never merges.
- **session:** your full Claude Code setup (skills, connectors, browser) in the vault folder. Tools that spend credits are blocked unless the errand carries a spending limit you approved.

**Code errands need permission to push your fork.** Use one of these:

- **GitHub CLI:**
  ```sh
  gh auth login
  gh auth setup-git
  ```
- **A fine-grained personal access token** (github.com → Settings → Developer settings → Fine-grained tokens):
  - Repository access: only your fork of this repo.
  - Permissions: Contents read and write, plus Pull requests read and write if you want the assistant to open PRs from another Claude session.
  - Use it as your git password for github.com, stored by your system's credential helper (`git config --global credential.helper osxkeychain` on a Mac). Never put the token in the settings file, the vault or the repo.

Review an errand's branch and merge it yourself on GitHub. The `[errands]` section of the settings file sets the limits: timeout, how many run at once, the model, which tools a session errand may use, and the browser it should use.

**Your name on commits.** Commits are public. If git on this computer uses an address you would rather not publish (`git config user.email`), set a GitHub noreply address for this repo (`git config user.email <id>+<you>@users.noreply.github.com`).

## 7. Make it yours

- **Names:** open the menu in the game (Esc) and choose **Character** to set your name, your companion's name, a portrait, the time zone and your password. Names and the time zone are written to `[names]` and `[tally]` in your settings file, the same file you can edit by hand. The code and the context files never name anyone. They say OWNER where your name goes and Tally where the companion's does, and the game fills in your names everywhere: on screen, in the login card, in what the companion is told, in tool texts, in the short check-ins and in errand briefs. Your companion's character itself comes from your `soul.md`.
- **Portrait:** put an image in `web/portraits/` and set `[names] owner_portrait = "portraits/<file>"`. The companion's portrait and animations are set in `world/tiles.json` under `creatures.tally`.
- **The map:** `world/vault.json` is the layout. Run `python3 -m obsidian_vault.check_map` after editing it. The companion can also move furniture and name places itself, and those changes live in `.vault-state/world.json`.
- **The rules for the game:** `context/the-vault-game.md` (talking in the game), `context/tally-tick.md` (the short check-ins between conversations) and `context/tally-guest.md` (the public version for guests). Edit them freely, and keep writing OWNER where your name should go.

## 8. Check that it works

```sh
python3 -m unittest discover -s tests
python3 -m obsidian_vault.check_map
```

The tests include a privacy guard (`tests/test_pii.py`). It fails if a file in the repo holds a home-folder path (such as `/Users/<name>/...`), an email address other than placeholders like `@example.com`, or a phone number. The tests never read or write your real settings file.

Then in the game:

1. Log in.
2. Walk up to the assistant (click it) and say hello.
3. Ask it to add a line to its interaction log. The line appears in `soul.md` at once.
4. Ask it to propose a small note edit. A yellow `!` appears in the top bar; open it and approve or reject the change.

## Privacy checklist

- The vault, your settings file (outside the game folder) and `.vault-state/` stay on your machine. Only the code and the game's context files are in the repo.
- Guests never see your vault paths or the Settings and Character windows.
- Everything the assistant reads in a conversation, including the core files and any note it opens, is sent to Claude as part of that conversation.
- With `writes.mode = "review"`, note changes wait for you. The assistant's own files, `soul.md` and the failure log, are written at once with a backup; set `soul_direct = false` to review those too.
- Keep tokens and passwords out of notes and out of the repo. Use your system's keychain.
