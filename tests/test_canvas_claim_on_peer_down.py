#!/usr/bin/env python3
"""When a federated canvas host drops, claim the mirror on local Cloudflare."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_canvas_claim_")
os.environ["SSHCHAT_CANVAS_STORE"] = os.path.join(tmpdir, "canvas_sessions.json")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")
os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"

import canvas_sharing  # noqa: E402
import server  # noqa: E402


class FakeHub:
    enabled = True
    node_id = "local-node"

    def known_peer_ids(self):
        return []


class FakeFileHttp:
    def get_base_url(self):
        return "https://phase-villa-ancient-effect.trycloudflare.com"


class CanvasClaimOnPeerDownTests(unittest.TestCase):
    def setUp(self) -> None:
        canvas_sharing.canvas_store.sessions.clear()
        canvas_sharing.canvas_store.token_to_session.clear()
        canvas_sharing.canvas_store.tickets.clear()

    def test_peer_down_claims_remote_mirror_on_local_cf(self) -> None:
        remote = canvas_sharing.canvas_store.register_remote_session(
            session_id="sid-remote",
            creator="alice",
            participants=["alice", "bob"],
            room="math",
            tokens={"alice": "tokA", "bob": "tokB"},
            keys={"alice": "KEYAAA", "bob": "KEYBBB"},
            host_node="Mathematics.local",
            host_base_url="https://should-documents-clouds-mandatory.trycloudflare.com",
        )
        notices = []
        invites = []

        with mock.patch.object(server.federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "file_http", FakeFileHttp()):
                with mock.patch.object(
                    server, "broadcast_room", side_effect=lambda r, b: notices.append((r, b))
                ):
                    with mock.patch.object(
                        server,
                        "_deliver_canvas_invites",
                        side_effect=lambda s, only=None: invites.append(s.session_id),
                    ):
                        with mock.patch.object(server, "_federation_push_canvas_announce"):
                            server._fed_handle_unreachable_canvas_authority(
                                "Mathematics.local"
                            )

        live = canvas_sharing.canvas_store.find_open_for_room("math")
        self.assertIsNotNone(live)
        self.assertIsNone(live.host_node)
        self.assertEqual(
            live.host_base_url,
            "https://phase-villa-ancient-effect.trycloudflare.com",
        )
        self.assertIsNotNone(canvas_sharing.canvas_store.get_by_token("tokA"))
        self.assertEqual(invites, ["sid-remote"])
        self.assertTrue(notices)
        self.assertIn("本机公网".encode("utf-8"), notices[0][1])
        self.assertIs(live, remote)

    def test_peer_down_prefers_parked_local_fork(self) -> None:
        local = canvas_sharing.canvas_store.create_session(
            creator="alice", participants=["bob"], room="math"
        )
        local.elements = [{"id": "keep-me"}]
        canvas_sharing.canvas_store.park_session(local.session_id)
        canvas_sharing.canvas_store.register_remote_session(
            session_id="sid-remote",
            creator="carol",
            participants=["carol"],
            room="math",
            tokens={"carol": "tokC"},
            keys={"carol": "KEYCCC"},
            host_node="Mathematics.local",
            host_base_url="https://dead.trycloudflare.com",
        )
        invites = []
        with mock.patch.object(server.federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "file_http", FakeFileHttp()):
                with mock.patch.object(server, "broadcast_room"):
                    with mock.patch.object(
                        server,
                        "_deliver_canvas_invites",
                        side_effect=lambda s, only=None: invites.append(s.session_id),
                    ):
                        with mock.patch.object(server, "_federation_push_canvas_announce"):
                            server._fed_handle_unreachable_canvas_authority(
                                "Mathematics.local"
                            )
        live = canvas_sharing.canvas_store.find_open_for_room("math")
        self.assertEqual(live.session_id, local.session_id)
        self.assertEqual(live.elements[0]["id"], "keep-me")
        self.assertEqual(invites, [local.session_id])


if __name__ == "__main__":
    unittest.main()
