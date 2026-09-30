#!/usr/bin/env python3
"""/fed — show federation peers and online users."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_fed_cmd_")
os.environ.setdefault("SSHCHAT_FILE_STORAGE_DIR", os.path.join(tmpdir, "files"))
# Do not force default locale — other tests expect English default.

import federation  # noqa: E402
import server  # noqa: E402


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data.decode("utf-8", errors="replace"))


class FakeHub:
    def __init__(
        self,
        *,
        enabled=True,
        node_id="local",
        direct=None,
        known=None,
        remote_by_node=None,
    ):
        self.enabled = enabled
        self.node_id = node_id
        self._direct = list(direct or [])
        self._known = list(known or self._direct)
        self._remote_by_node = dict(remote_by_node or {})

    @property
    def peer_count(self) -> int:
        return len(self._direct)

    def direct_peer_ids(self):
        return list(self._direct)

    def known_peer_ids(self):
        return list(self._known)

    def remote_users_by_node(self):
        return {
            node: list(rows) for node, rows in self._remote_by_node.items()
        }


class FedCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = DummyConn()
        server.clients[self.conn] = {
            "name": "alice",
            "rooms": {"default"},
            "current_room": "default",
            "locale": "zh",
        }

    def tearDown(self) -> None:
        server.clients.pop(self.conn, None)

    def _run(self, payload: str) -> str:
        self.conn.sent.clear()
        server.handle_command(self.conn, payload)
        return "".join(self.conn.sent)

    def test_disabled(self) -> None:
        with mock.patch.object(server.federation, "get_hub", return_value=None):
            out = self._run("/fed")
        self.assertIn("未启用联邦", out)

    def test_none_online_still_lists_local_users(self) -> None:
        hub = FakeHub(direct=[])
        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            out = self._run("/peers")
        self.assertIn("没有直连在线", out)
        self.assertIn("联邦在线用户", out)
        self.assertIn("alice(#default)", out)

    def test_lists_direct_reachable_and_users(self) -> None:
        hub = FakeHub(
            node_id="A.local",
            direct=["B.local"],
            known=["B.local", "C.local"],
            remote_by_node={
                "B.local": [("bob", "lobby")],
                "C.local": [("carol", "default")],
            },
        )
        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            out = self._run("/federation")
        self.assertIn("直连在线 1", out)
        self.assertIn("B.local", out)
        self.assertIn("经路由可达", out)
        self.assertIn("C.local", out)
        self.assertIn("联邦在线用户共 3", out)
        self.assertIn("alice(#default)", out)
        self.assertIn("bob(#lobby)", out)
        self.assertIn("carol(#default)", out)

    def test_help(self) -> None:
        with mock.patch.object(server.federation, "get_hub", return_value=FakeHub()):
            out = self._run("/fed help")
        self.assertIn("/fed", out)
        self.assertIn("/peers", out)
        self.assertIn("用户", out)


if __name__ == "__main__":
    unittest.main()
