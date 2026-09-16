#!/usr/bin/env python3
"""Re-deliver canvas invites when Quick Tunnel public base URL moves."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_canvas_pub_")
os.environ["SSHCHAT_CANVAS_STORE"] = os.path.join(tmpdir, "canvas_sessions.json")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")
os.environ["SSHCHAT_CANVAS_PUBLIC_BASE_FILE"] = os.path.join(
    tmpdir, "last_canvas_invite_base"
)
os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"

import canvas_sharing  # noqa: E402
import server  # noqa: E402


class FakeFileHttp:
    def __init__(self, base: str) -> None:
        self._base = base

    def get_base_url(self) -> str:
        return self._base


class CanvasPublicRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        canvas_sharing.canvas_store.sessions.clear()
        canvas_sharing.canvas_store.token_to_session.clear()
        canvas_sharing.canvas_store.tickets.clear()
        server._LAST_CANVAS_PUBLIC_BASE = ""
        try:
            os.unlink(os.environ["SSHCHAT_CANVAS_PUBLIC_BASE_FILE"])
        except OSError:
            pass

    def test_maybe_refresh_redelivers_when_base_changes(self) -> None:
        session = canvas_sharing.canvas_store.create_session(
            creator="alice", participants=["bob"], room="lobby"
        )
        invited: list[str] = []

        with mock.patch.object(
            server,
            "file_http",
            FakeFileHttp("https://new-host.trycloudflare.com"),
        ):
            with mock.patch.object(
                server,
                "_deliver_canvas_invites",
                side_effect=lambda s, only=None, refreshed=False: invited.append(
                    f"{s.session_id}:{refreshed}"
                ),
            ):
                with mock.patch.object(server, "_federation_push_all_canvas_announces"):
                    server._maybe_refresh_canvas_invites_on_public_change(
                        "https://new-host.trycloudflare.com", reason="boot"
                    )
                    # Same base again: no second wave.
                    server._maybe_refresh_canvas_invites_on_public_change(
                        "https://new-host.trycloudflare.com", reason="boot"
                    )

        self.assertEqual(invited, [f"{session.session_id}:True"])
        self.assertEqual(
            server._load_last_canvas_public_base(),
            "https://new-host.trycloudflare.com",
        )

    def test_local_deliver_uses_live_base_not_frozen_host(self) -> None:
        session = canvas_sharing.canvas_store.create_session(
            creator="alice", participants=["alice"], room="lobby"
        )
        session.host_base_url = "https://old-dead.trycloudflare.com"
        sent: list[str] = []

        def capture(conn, text):
            sent.append(text)

        with mock.patch.object(
            server,
            "file_http",
            FakeFileHttp("https://fresh.trycloudflare.com"),
        ):
            with mock.patch.object(server, "clients", {}):
                with mock.patch.object(server, "send_line", side_effect=capture):
                    with mock.patch.object(server.federation, "get_hub", return_value=None):
                        server._deliver_canvas_invites(session)

        # No local clients — nothing sent — but URL construction must use live base.
        # Re-run with a fake client.
        class Conn:
            pass

        conn = Conn()
        with mock.patch.object(
            server,
            "file_http",
            FakeFileHttp("https://fresh.trycloudflare.com"),
        ):
            with mock.patch.object(
                server,
                "clients",
                {conn: {"name": "alice"}},
            ):
                with mock.patch.object(server, "send_line", side_effect=capture):
                    with mock.patch.object(server.federation, "get_hub", return_value=None):
                        server._deliver_canvas_invites(session)

        self.assertTrue(sent)
        self.assertIn("https://fresh.trycloudflare.com/canvas/", sent[-1])
        self.assertNotIn("old-dead.trycloudflare.com", sent[-1])


if __name__ == "__main__":
    unittest.main()
