import socket
import threading
import sys
import os
import pwd

from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout

SERVER_IP = os.environ.get("SSHCHAT_SERVER", "127.0.0.1")
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

name = pwd.getpwuid(os.getuid()).pw_name


def recv_msg(sock):
    while True:
        try:
            data = sock.recv(1024)
            if not data:
                print("\n[ERROR] server disconnected")
                break

            print(data.decode("utf-8"), end="")

        except Exception:
            print("\n[ERROR] receive failed")
            break

    sock.close()
    os._exit(0)


def main():
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

    threading.Thread(target=recv_msg, args=(s,), daemon=True).start()

    with patch_stdout():
        while True:
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

    s.close()


if __name__ == "__main__":
    main()
