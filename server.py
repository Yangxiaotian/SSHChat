import socket
import threading

# connection -> username
clients = {}
lock = threading.Lock()


def broadcast(msg):
    """Broadcast message to all clients"""
    with lock:
        for conn in list(clients.keys()):
            try:
                conn.send(msg)
            except Exception:
                remove_client(conn)


def remove_client(conn):
    """Remove a client"""
    with lock:
        name = clients.get(conn, "Unknown")

        print(f"{name} disconnected")

        # broadcast leave message
        leave_msg = f"[!] {name} left the chat\n".encode("utf-8")

        for c in list(clients.keys()):
            try:
                c.send(leave_msg)
            except Exception:
                pass

        if conn in clients:
            del clients[conn]

    try:
        conn.close()
    except Exception:
        pass


def handle_client(conn, addr):
    try:
        # step 1: receive username
        name = conn.recv(1024).decode("utf-8").strip()

        if not name:
            name = "Unknown"

        with lock:
            clients[conn] = name

        print(f"{name} joined ({addr})")

        # broadcast join message
        join_msg = f"[+] {name} joined the chat\n".encode("utf-8")
        broadcast(join_msg)

        # chat loop
        while True:
            msg = conn.recv(1024)
            if not msg:
                break
            broadcast(msg)

    except Exception as e:
        print("connection error:", e)
    finally:
        remove_client(conn)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # reuse port
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    s.bind(("0.0.0.0", 12345))
    s.listen()

    print("chat server started on port 12345")

    while True:
        conn, addr = s.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True
        ).start()


if __name__ == "__main__":
    main()
