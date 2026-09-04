# SSHChat

**Languages:** English (this file) · [中文说明](README.zh.md)

SSHChat is a multi-user chat server over SSH/TCP with room chat, private messages, offline leave-messages, secure HTTP file transfer, a shared drawing board (`/canvas`), RSS news, a small library reader, dictionary lookup, and room mini-games (chess, gomoku, go, xiangqi, poker variants, werewolf, and more). An optional Electron GUI is included.

**UI language defaults to English.** Switch with `/lang zh` (preference is saved per nickname). The Electron client defaults to English and syncs `/lang` on connect. **Sanguosha (`sanguo`) move/skill text is still mostly Chinese** in this release; other games localize at send time.

---

## Quick start (users)

Ask your admin for host, SSH port, and username, then:

```bash
ssh -p <port> <user>@<host>
```

On first connect you will see an active-room tip line. Useful commands:

| Command | Purpose |
|---------|---------|
| `/help` | Full command help |
| `/lang en` / `/lang zh` | UI language (saved per nick) |
| `/names` | Users in the active room |
| `/rooms` | Rooms you joined (`*` = active) |
| `/join <room>` | Join and switch |
| `/msg <nick> <text>` | PM (or leave-message if offline) |
| `/sendfile` | File to current room |
| `/sendfile <nick>` | File to a user |
| `/canvas` | Shared drawing board (current room) |
| `/canvas <nick>` | Private board with one user |
| `/poll` | Show room poll |
| `/poll new Q \| A \| B` | Start a poll (`\|`-separated options) |
| `/poll <n>` | Vote (may change) |
| `/poll close` | Close poll (creator or room owner) |
| `/later 30m <text>` | Personal reminder (only you; also `2h`/`1d`, `09:30`, `tomorrow 09:00`) |
| `/later list` | List your pending reminders |
| `/later cancel <n>` | Cancel one |
| `/game help` | Mini-game help |
| `/news` | RSS headlines |
| `/library` | Browse books |
| `/dict <word>` | Dictionary |
| `/dnd on` | Terminal do-not-disturb for game spam |

Beginner phone client (iSH): [docs/en/ish-beginner.md](docs/en/ish-beginner.md) · Chinese: [小白使用说明书-iSH.md](小白使用说明书-iSH.md)

File sharing guide: [docs/en/file-sharing.md](docs/en/file-sharing.md) · Chinese: [USER_GUIDE_FILE_SHARING.md](USER_GUIDE_FILE_SHARING.md)

---

## Deploy (admins)

```bash
# Typical Linux VPS
./deploy.sh

# iSH (Alpine on iPhone/iPad) as a *server*
# See docs/en/deploy-ish.md and DEPLOY-iSH.md
```

Common env vars:

| Variable | Meaning |
|----------|---------|
| `SSHCHAT_PORT` | Chat TCP port (default `12345`) |
| `SSHCHAT_DEFAULT_LOCALE` | `en` (default) or `zh` |
| `SSHCHAT_LOCALE_STORE` | Path for per-nick language prefs (default `user_locales.json`) |
| `SSHCHAT_RATING_STORE` | Game ratings JSON |
| `SSHCHAT_SESSION_STORE` | In-progress game sessions |
| `SSHCHAT_OFFLINE_MSG_STORE` | Offline leave-messages |

Federation (multi-server), users, firewall, and Cloudflare file HTTPS are documented in detail in [README.zh.md](README.zh.md).

iSH server notes: [docs/en/deploy-ish.md](docs/en/deploy-ish.md) · [DEPLOY-iSH.md](DEPLOY-iSH.md)

---

## Mini-games

Games are **per room** (one active match at a time). Host can `/game on|off <name>`.

| Id | Notes | Aliases |
|----|-------|---------|
| `chess` | International chess | — |
| `gomoku` | 15×15 | — |
| `go` | 19×19 | `weiqi`, `baduk` |
| `xiangqi` | Chinese chess | `cchess` |
| `sanguo` | Sanguosha (UI mostly ZH for now) | `sgs`, `三国杀` |
| `werewolf` | 5–12 players | `langrensha` |
| `holdem` | Texas Hold'em | `poker`, `texas` |
| `zjh` | Zha Jin Hua | `zhajinhua` |
| `niutou` | Niu Tou Wang | — |
| `mahjong` | 4 players (+ AI fill) | — |
| `doushou` | Jungle / Dou Shou Qi | — |

Typical flow:

```text
/game new chess
/game join
/game move e2e4
/game show
/game resign
```

Many move verbs accept Chinese or English aliases (e.g. holdem `跟注` / `call`).

---

## Electron GUI

```bash
cd electron
npm ci
npm run dev
```

Portable Windows build: `npm run build:portable` → `electron/release/`.

The GUI has its own locale toggle (default **English**). On connect it sends `/lang` so server chat text matches.

---

## Shared drawing board

`/canvas` (alias `/board`) opens a shared web whiteboard on the same HTTP(S) file service as `/sendfile`. Each participant gets a **unique URL** and a **separate 6-character key** (the key is never in the URL).

| Command | Purpose |
|---------|---------|
| `/canvas` | Board for the current room |
| `/canvas <nick>` | Private board with an online user |
| `/canvas #<room>` | Board for a room you are in |
| `/canvas close` | Creator closes the current room board |
| `/canvas new` | Force a new board even if the room already has one |
| `/canvas help` | Short usage reminder |

Open the URL in a browser, enter the key, then draw; strokes sync live. GUI clients (tk / Electron) open the board in-app. Sessions expire after a few hours (default 4). Same Cloudflare / federation-proxy rules as `/sendfile`.

---

## File transfer security (summary)

`/sendfile` opens a one-time HTTP(S) upload/download page. Keys are **not** embedded in URLs; upload and download tokens are single-use. Details: [docs/en/file-sharing.md](docs/en/file-sharing.md).

Upload/download HTML defaults to English; add `?lang=zh` for Chinese pages. The shared canvas uses the same port and key-not-in-URL pattern; canvas access tickets are multi-use until expiry so people can keep drawing.

---

## Development

```bash
python3 -m unittest tests.test_i18n
python3 -m unittest tests.test_client_dnd
python3 test_file_preview.py
```

Locale catalogs live under `locales/` (`en.py`, `zh.py`, `game_phrases.py`). Core API: `i18n.py`, persistence: `locale_store.py`.

---

## License

See [LICENSE](LICENSE).
