## Cursor Cloud specific instructions

### Project overview

SSHChat is a self-hosted text chat room running over SSH. See `README.md` (Chinese) for full docs.

### Key components

| Component | File | Dependencies |
|---|---|---|
| Chat TCP server | `server.py` | stdlib only |
| Terminal TUI client | `client.py` | `prompt_toolkit` |
| GUI client | `sshchat_gui.py` | `tkinter`, `paramiko` |
| Client config util | `sshchat_client_util.py` | stdlib only |
| SSH launcher | `easy_connect.py` | stdlib only |

### Dev environment

A Python 3 venv at `/workspace/venv` contains all runtime deps (`prompt_toolkit`, `paramiko`). Activate with `source venv/bin/activate`.

### Running services

- **Server**: `source venv/bin/activate && python3 server.py` — listens on TCP port 12345 (override via `SSHCHAT_PORT` env var). No external services (databases, caches) required; all state is in-memory.
- **Terminal client**: `source venv/bin/activate && python3 client.py` — connects to `127.0.0.1:12345` by default (override via `SSHCHAT_SERVER` / `SSHCHAT_PORT` env vars). Requires a TTY for `prompt_toolkit`; falls back to `stdin.readline` in non-interactive mode.
- **GUI client**: `source venv/bin/activate && python3 sshchat_gui.py --full-ui` — opens a Tkinter window. Needs a display (`$DISPLAY` or Xvfb).

### Lint / syntax checks

No formal test suite or linter config exists. Use `python3 -m pyflakes *.py` and `python3 -m py_compile <file>` for quick checks.

### Gotchas

- `client.py` imports `pwd` (Unix-only) to get the current username; it will not work on Windows.
- The server has no graceful shutdown — terminate with Ctrl-C or signal; clients will reconnect if using `chat.sh`.
- `deploy.sh` is for production installs (requires `sudo`); do not run it in the dev environment.
