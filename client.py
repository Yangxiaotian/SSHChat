import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import threading

from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout

SERVER_IP = os.environ.get("SSHCHAT_SERVER", "127.0.0.1")
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

name = pwd.getpwuid(os.getuid()).pw_name

# beep: terminal bell | notify: desktop notification (macOS / Linux) | all | none
_ALERT = (os.environ.get("SSHCHAT_ALERT") or "beep").strip().lower()
_CHAT_PREFIX = re.compile(r"^\[([^\]]+)\]\s+(.*)$")
_SYSTEM_SENDERS = frozenset(("+", "!", "*"))
_STOP = threading.Event()
_DISCONNECTED = threading.Event()


def _alert_beep() -> None:
    # Terminal bell first; some terminals mute this by default.
    print("\a", end="", flush=True)
    # macOS audible fallback when terminal bell is disabled.
    if shutil.which("osascript"):
        subprocess.run(
            ["osascript", "-e", "beep 1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    # Stronger macOS fallback for SSH sessions: play a system sound file.
    if shutil.which("afplay"):
        sound = "/System/Library/Sounds/Glass.aiff"
        if os.path.exists(sound):
            subprocess.run(
                ["afplay", sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


def _alert_notify(sender: str, preview: str) -> None:
    title = "SSHChat"
    subtitle = sender
    body = preview if preview else "(message)"
    if shutil.which("osascript"):
        # argv avoids brittle AppleScript string escaping
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv\n"
                '\tdisplay notification (item 1 of argv) with title '
                "(item 2 of argv) subtitle (item 3 of argv)\n"
                "end run",
                body[:400],
                title,
                subtitle[:200],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    elif shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", title, f"{title} — {subtitle}", body[:400]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def maybe_alert_incoming(sender: str, preview: str) -> None:
    if _ALERT in ("", "none", "0", "off"):
        return
    if _ALERT in ("beep", "all", "both"):
        _alert_beep()
    if _ALERT in ("notify", "all", "both"):
        _alert_notify(sender, preview)


def _line_is_peer_chat(line: str, my_name: str) -> tuple[bool, str, str]:
    """Return (is_peer_chat, sender, preview) for a single line without trailing \\n."""
    m = _CHAT_PREFIX.match(line.rstrip("\r"))
    if not m:
        return False, "", ""
    sender, rest = m.group(1), m.group(2)
    if sender in _SYSTEM_SENDERS or sender == my_name:
        return False, sender, rest
    return True, sender, rest


def recv_msg(sock, my_name: str):
    notif_buf = ""
    while True:
        try:
            data = sock.recv(1024)
            if not data:
                print("\n[ERROR] server disconnected")
                _DISCONNECTED.set()
                break

            text = data.decode("utf-8", errors="replace")
            print(text, end="", flush=True)

            notif_buf += text
            while "\n" in notif_buf:
                line, notif_buf = notif_buf.split("\n", 1)
                ok, sender, preview = _line_is_peer_chat(line, my_name)
                if ok:
                    maybe_alert_incoming(sender, preview)

        except Exception:
            print("\n[ERROR] receive failed")
            _DISCONNECTED.set()
            break

    sock.close()
    _STOP.set()


def main():
    _STOP.clear()
    _DISCONNECTED.clear()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        s.connect((SERVER_IP, PORT))
    except Exception:
        print("[ERROR] cannot connect to server")
        return

    # send username
    s.send((name + "\n").encode("utf-8"))

    print("[OK] connected as " + name)
    print("Commands: /users  /join <room>  /help")
    print(
        f"Alerts (SSHCHAT_ALERT={_ALERT}): beep | notify | all | none — "
        "peer chat lines only"
    )

    threading.Thread(target=recv_msg, args=(s, name), daemon=True).start()

    # Some forced-command SSH sessions may not provide a TTY for prompt_toolkit.
    use_prompt_toolkit = sys.stdin.isatty() and sys.stdout.isatty()
    if not use_prompt_toolkit:
        print("[*] non-interactive terminal detected; fallback input mode")

    if use_prompt_toolkit:
        with patch_stdout():
            while True:
                if _STOP.is_set():
                    print("[INFO] disconnected")
                    break
                try:
                    msg = prompt("> ")

                    if msg.strip() == "":
                        continue

                    s.send(("[" + name + "] " + msg + "\n").encode("utf-8"))

                except (KeyboardInterrupt, EOFError):
                    print("\n[INFO] exit")
                    break
                except Exception:
                    print("[ERROR] send failed")
                    break
    else:
        while True:
            if _STOP.is_set():
                print("[INFO] disconnected")
                break
            try:
                sys.stdout.write("> ")
                sys.stdout.flush()
                msg = sys.stdin.readline()
                if msg == "":
                    print("\n[INFO] stdin closed")
                    break
                msg = msg.rstrip("\r\n")
                if msg.strip() == "":
                    continue
                s.send(("[" + name + "] " + msg + "\n").encode("utf-8"))
            except (KeyboardInterrupt, EOFError):
                print("\n[INFO] exit")
                break
            except Exception:
                print("[ERROR] send failed")
                break

    s.close()
    if _DISCONNECTED.is_set():
        # Let chat.sh decide whether to auto-restart client.
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
