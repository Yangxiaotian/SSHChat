#!/usr/bin/env python3
"""Sticky Cloudflare host election + canvas/piano park-and-join same base_url."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmpdir = tempfile.mkdtemp(prefix="sshchat_sticky_cf_")
os.environ["SSHCHAT_CANVAS_STORE"] = os.path.join(tmpdir, "canvas_sessions.json")
os.environ["SSHCHAT_PIANO_STORE"] = os.path.join(tmpdir, "piano_sessions.json")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")
os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"

import canvas_sharing  # noqa: E402
import federation  # noqa: E402
import piano_sharing  # noqa: E402
import server  # noqa: E402


def _make_hub(node_id: str, *, local_url: str = "") -> federation.FederationHub:
    hub = federation.FederationHub(
        12345,
        server.lock,
        lambda r, m, p: None,
        lambda r, m: None,
        lambda t, f, x: None,
        lambda: [],
    )
    hub.enabled = True
    hub.node_id = node_id
    if local_url:
        hub.get_local_file_public = lambda: local_url
    return hub


class StickyFileHostTests(unittest.TestCase):
    def test_both_nodes_elect_same_min_node_id(self) -> None:
        a = _make_hub(
            "K56CM.local",
            local_url="https://k56.trycloudflare.com",
        )
        a._routes["Mathematics.local"] = "Mathematics.local"
        a._remote_file_pubs["Mathematics.local"] = {
            "base_url": "https://math.trycloudflare.com",
            "seen_at": 200.0,
        }

        b = _make_hub(
            "Mathematics.local",
            local_url="https://math.trycloudflare.com",
        )
        b._routes["K56CM.local"] = "K56CM.local"
        b._remote_file_pubs["K56CM.local"] = {
            "base_url": "https://k56.trycloudflare.com",
            "seen_at": 100.0,
        }

        self.assertEqual(a.pick_federation_file_host()[0], "K56CM.local")
        self.assertEqual(b.pick_federation_file_host()[0], "K56CM.local")
        self.assertEqual(
            a.pick_federation_file_host()[1],
            "https://k56.trycloudflare.com",
        )

    def test_sticky_keeps_node_when_url_refreshes(self) -> None:
        hub = _make_hub("node-z", local_url="https://z.trycloudflare.com")
        hub._routes["node-a"] = "node-a"
        hub._remote_file_pubs["node-a"] = {
            "base_url": "https://a-old.trycloudflare.com",
            "seen_at": 1.0,
        }
        first = hub.pick_federation_file_host()
        self.assertEqual(first, ("node-a", "https://a-old.trycloudflare.com"))
        hub._remote_file_pubs["node-a"]["base_url"] = "https://a-new.trycloudflare.com"
        second = hub.pick_federation_file_host()
        self.assertEqual(second, ("node-a", "https://a-new.trycloudflare.com"))

    def test_sticky_re_elects_when_host_drops(self) -> None:
        hub = _make_hub("node-z", local_url="https://z.trycloudflare.com")
        hub._routes["node-a"] = "node-a"
        hub._routes["node-m"] = "node-m"
        hub._remote_file_pubs["node-a"] = {
            "base_url": "https://a.trycloudflare.com",
            "seen_at": 1.0,
        }
        hub._remote_file_pubs["node-m"] = {
            "base_url": "https://m.trycloudflare.com",
            "seen_at": 2.0,
        }
        self.assertEqual(hub.pick_federation_file_host()[0], "node-a")
        hub._remote_file_pubs.pop("node-a")
        hub._routes.pop("node-a")
        # Remaining: node-m (peer) + node-z (self) → min is node-m
        self.assertEqual(
            hub.pick_federation_file_host(),
            ("node-m", "https://m.trycloudflare.com"),
        )


class CanvasStickyProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        canvas_sharing.canvas_store.sessions.clear()
        canvas_sharing.canvas_store.token_to_session.clear()
        canvas_sharing.canvas_store.tickets.clear()

    def test_create_proxies_to_sticky_even_when_local_cf_ready(self) -> None:
        hub = _make_hub(
            "Mathematics.local",
            local_url="https://math.trycloudflare.com",
        )
        hub._routes["K56CM.local"] = "K56CM.local"
        hub._remote_file_pubs["K56CM.local"] = {
            "base_url": "https://k56.trycloudflare.com",
            "seen_at": 1.0,
        }
        lines: list[str] = []

        class FakeConn:
            pass

        class FakeFileHttp:
            def get_base_url(self):
                return "https://math.trycloudflare.com"

        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            with mock.patch.object(server, "file_http", FakeFileHttp()):
                with mock.patch.object(
                    server, "send_line", side_effect=lambda c, m: lines.append(m)
                ):
                    with mock.patch.object(
                        server,
                        "_federation_request_canvas_host",
                        return_value={
                            "ok": True,
                            "session_id": "sid-sticky",
                            "base_url": "https://k56.trycloudflare.com",
                            "creator": "alice",
                            "tokens": {"alice": "tokA", "bob": "tokB"},
                            "keys": {"alice": "KEYAAA", "bob": "KEYBBB"},
                            "conflict_token": "ctok",
                            "rev": 1,
                            "expires": 0,
                        },
                    ):
                        session = server._create_canvas_via_federation_proxy(
                            FakeConn(),
                            "alice",
                            ["alice", "bob"],
                            "lobby",
                        )
        self.assertIsNotNone(session)
        self.assertEqual(session.host_node, "K56CM.local")
        self.assertEqual(session.host_base_url, "https://k56.trycloudflare.com")
        self.assertTrue(any("K56CM.local" in (m or "") for m in lines))

    def test_csync_conflict_parks_and_adopts_same_base_url(self) -> None:
        local = canvas_sharing.canvas_store.create_session(
            creator="alice", participants=["bob"], room="lobby"
        )
        announce = {
            "session_id": "sid-remote",
            "room": "lobby",
            "creator": "carol",
            "host_node": "K56CM.local",
            "base_url": "https://k56.trycloudflare.com",
            "conflict_token": "zzzz",
            "tokens": {"carol": "tokC"},
            "keys": {"carol": "KEYCCC"},
            "rev": 2,
            "expires": 0,
        }
        # Force remote win: higher conflict_token wins.
        local.conflict_token = "aaaa"
        hub = _make_hub("Mathematics.local")
        notices: list[bytes] = []
        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            with mock.patch.object(
                server,
                "broadcast_room",
                side_effect=lambda r, b: notices.append(b),
            ):
                server._fed_on_canvas_sync("K56CM.local", announce)
        parked = canvas_sharing.canvas_store.find_parked_for_room("lobby")
        live = canvas_sharing.canvas_store.find_open_for_room("lobby")
        self.assertIsNotNone(parked)
        self.assertEqual(parked.session_id, local.session_id)
        self.assertIsNotNone(live)
        self.assertEqual(live.session_id, "sid-remote")
        self.assertEqual(live.host_base_url, "https://k56.trycloudflare.com")
        self.assertTrue(notices)


class PianoStickyProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        piano_sharing.piano_store.sessions.clear()
        piano_sharing.piano_store.token_to_session.clear()
        piano_sharing.piano_store.tickets.clear()

    def test_create_proxies_to_sticky_even_when_local_cf_ready(self) -> None:
        hub = _make_hub(
            "Mathematics.local",
            local_url="https://math.trycloudflare.com",
        )
        hub._routes["K56CM.local"] = "K56CM.local"
        hub._remote_file_pubs["K56CM.local"] = {
            "base_url": "https://k56.trycloudflare.com",
            "seen_at": 1.0,
        }

        class FakeConn:
            pass

        class FakeFileHttp:
            def get_base_url(self):
                return "https://math.trycloudflare.com"

        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            with mock.patch.object(server, "file_http", FakeFileHttp()):
                with mock.patch.object(server, "send_line"):
                    with mock.patch.object(
                        server,
                        "_federation_request_piano_host",
                        return_value={
                            "ok": True,
                            "session_id": "pid-sticky",
                            "base_url": "https://k56.trycloudflare.com",
                            "creator": "alice",
                            "tokens": {"alice": "tokA", "bob": "tokB"},
                            "keys": {"alice": "KEYAAA", "bob": "KEYBBB"},
                            "conflict_token": "ctok",
                            "rev": 1,
                            "expires": 0,
                        },
                    ):
                        session = server._create_piano_via_federation_proxy(
                            FakeConn(),
                            "alice",
                            ["alice", "bob"],
                            "lobby",
                        )
        self.assertIsNotNone(session)
        self.assertEqual(session.host_node, "K56CM.local")
        self.assertEqual(session.host_base_url, "https://k56.trycloudflare.com")

    def test_pisync_conflict_parks_and_adopts_same_base_url(self) -> None:
        local = piano_sharing.piano_store.create_session(
            creator="alice", participants=["bob"], room="lobby"
        )
        local.conflict_token = "aaaa"
        announce = {
            "session_id": "pid-remote",
            "room": "lobby",
            "creator": "carol",
            "host_node": "K56CM.local",
            "base_url": "https://k56.trycloudflare.com",
            "conflict_token": "zzzz",
            "tokens": {"carol": "tokC"},
            "keys": {"carol": "KEYCCC"},
            "rev": 2,
            "expires": 0,
        }
        hub = _make_hub("Mathematics.local")
        with mock.patch.object(server.federation, "get_hub", return_value=hub):
            with mock.patch.object(server, "broadcast_room"):
                server._fed_on_piano_sync("K56CM.local", announce)
        parked = piano_sharing.piano_store.find_parked_for_room("lobby")
        live = piano_sharing.piano_store.find_open_for_room("lobby")
        self.assertIsNotNone(parked)
        self.assertEqual(parked.session_id, local.session_id)
        self.assertIsNotNone(live)
        self.assertEqual(live.session_id, "pid-remote")
        self.assertEqual(live.host_base_url, "https://k56.trycloudflare.com")

    def test_deliver_piano_invite_uses_host_base_url(self) -> None:
        session = piano_sharing.piano_store.register_remote_session(
            session_id="pid-m",
            creator="alice",
            participants=["alice"],
            room="lobby",
            tokens={"alice": "tokA"},
            keys={"alice": "KEYAAA"},
            host_node="K56CM.local",
            host_base_url="https://k56.trycloudflare.com",
        )
        delivered: list[str] = []

        class FakeConn:
            pass

        fake_clients = {FakeConn(): {"name": "alice"}}
        with mock.patch.object(server, "clients", fake_clients):
            with mock.patch.object(server, "lock", server.lock):
                with mock.patch.object(
                    server,
                    "send_line",
                    side_effect=lambda c, m: delivered.append(m),
                ):
                    with mock.patch.object(
                        server.federation, "get_hub", return_value=None
                    ):
                        server._deliver_piano_invites(session)
        self.assertTrue(delivered)
        self.assertIn("https://k56.trycloudflare.com/piano/tokA", delivered[0])


if __name__ == "__main__":
    unittest.main()
