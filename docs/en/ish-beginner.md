# SSHChat on iPhone / iPad (iSH client)

For people who have never used a terminal. Keys are usually already set up by your admin—you only need to connect.

Chinese original: [小白使用说明书-iSH.md](../../小白使用说明书-iSH.md)

---

## Three things you need

Get these from your admin:

| Item | Example | Yours |
|------|---------|-------|
| Server host | `stdlib.gicp.net` | ________ |
| SSH port | `44681` | ________ |
| Username | e.g. `yxt` | ________ |

Connect with:

```text
ssh -p <port> <username>@<host>
```

---

## 1. Install iSH

1. App Store → search **iSH** → install.
2. Open iSH (black terminal, virtual keyboard at the bottom).
3. Wait if it downloads Alpine system files the first time.

## 2. Install the SSH client (once)

```bash
apk update
apk add openssh-client
```

## 3. Log in

```bash
ssh -p 44681 youruser@your.host.example
```

First time, type `yes` when asked to continue connecting. Enter your password if prompted (or use the key your admin prepared).

You should see an active-room tip, for example:

```text
[*] Active room #default. ... /lang /help
```

Type plain text to chat. Try `/help`, `/names`, `/lang zh` (Chinese UI), `/lang en` (English UI).

Room poll example: `/poll new dinner? | pizza | sushi`, then `/poll 1` to vote; `/poll close` to end (creator or room owner).

Personal reminder: `/later 30m bring an umbrella` (only you see it); `/later list`; `/later cancel 1`.

## 4. Common tips

- **Chinese input:** use the system Chinese keyboard from the iSH keyboard icon.
- **Disconnect:** `exit` or close the session.
- **Reconnect:** run the same `ssh -p …` command again.
- If the host key changes after a server rebuild, remove the old entry as your admin instructs (`ssh-keygen -R …`).

## 5. Optional: keep the session alive

In `~/.ssh/config` (create if needed):

```text
Host sshchat
    HostName your.host.example
    Port 44681
    User youruser
    ServerAliveInterval 30
```

Then: `ssh sshchat`.

## 6. Shared drawing board

Type `/canvas` in chat. You get a **URL** and a **6-character key on a separate line**. Copy the URL into Safari (iSH cannot open the web page), enter the key, then draw. Strokes sync to everyone who unlocked the board.

- Current room: `/canvas`
- One online user: `/canvas theirnick`
- Close (creator): `/canvas close`
- Alias: `/board`

If the page will not load, it is the same network issue as `/sendfile` (the phone must reach the server’s file HTTP port). Ask your admin.

---

More features (games, files, library): see the main [README](../../README.md).
