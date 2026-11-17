"""Server outbound sends must not block forever — and must not settimeout()."""

from __future__ import annotations

import socket
import unittest
from unittest import mock

import server


class _FakeConn:
    """Non-selectable test double — federation path falls back to sendall."""

    def __init__(self) -> None:
        self.timeout: float | None = None
        self.sent: list[bytes] = []
        self.timeout_history: list[float | None] = []

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, value: float | None) -> None:
        self.timeout = value
        self.timeout_history.append(value)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class SocketSendTimeoutTests(unittest.TestCase):
    def test_socket_send_does_not_mutate_timeout(self) -> None:
        conn = _FakeConn()
        conn.timeout = None
        with mock.patch.object(server, "_SEND_TIMEOUT_SECONDS", 5.0):
            server._socket_send(conn, b"hello\n")
        self.assertEqual(conn.sent, [b"hello\n"])
        self.assertEqual(conn.timeout_history, [])
        self.assertIsNone(conn.timeout)

    def test_socket_send_timeout_propagates(self) -> None:
        conn = _FakeConn()

        def boom(_data: bytes) -> None:
            raise TimeoutError("timed out")

        conn.sendall = boom  # type: ignore[method-assign]
        with mock.patch.object(server, "_SEND_TIMEOUT_SECONDS", 1.0):
            with self.assertRaises(TimeoutError):
                server._socket_send(conn, b"x")
        self.assertEqual(conn.timeout_history, [])
        self.assertIsNone(conn.timeout)

    def test_socket_send_zero_timeout_uses_blocking_sendall(self) -> None:
        conn = _FakeConn()
        with mock.patch.object(server, "_SEND_TIMEOUT_SECONDS", 0.0):
            server._socket_send(conn, b"z\n")
        self.assertEqual(conn.sent, [b"z\n"])
        self.assertEqual(conn.timeout_history, [])


if __name__ == "__main__":
    unittest.main()
